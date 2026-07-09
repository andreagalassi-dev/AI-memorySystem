"""Fase 1.1 — Ibridazione: router esteso + fusione RRF cross-segnale su (doc, page).

Il retrieval vettoriale visivo e' pessimo sui codici alfanumerici esatti; BM25 sparse
resta indispensabile. Il router deterministico gia' esistente
(`retrieval.router.classify_query`) decide la corsia:

  - `code`            -> **BM25 puro** (un codice non si "vede", si matcha lessicalmente).
  - `natural`|`visual`-> **PixelRAG puro** (domanda visiva: esploso/schema).
  - `mixed`           -> **entrambi + fusione RRF** sulla chiave `(doc, page)`.

Perche' RRF e non una somma pesata: i punteggi assoluti di BM25 e di un embedder
visivo sono su scale **incomparabili**; si fondono i RANK, non i punteggi.
Formula standard: contributo di un elemento a rank `r` (1-based) in una lista =
`1 / (rrf_k + r)`. Un elemento **assente** da una lista non riceve contributo da
quella lista: e' esattamente la penalita' corretta (chi compare in entrambe batte,
a parita' di rank, chi compare in una sola).

hybrid.py e' PURO: riceve due funzioni di ricerca (che restituiscono liste ordinate
di chiavi `(doc, page)`) e le fonde. Il cablaggio ai retriever reali (BM25 di
`search.py`, `pixelrag.search.visual_search`) avviene a valle, cosi' la fusione e'
testabile senza Qdrant. Design: docs/superpowers/specs/2026-07-09-fase1.1-pixelrag-design.md.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.common.roles import Role
from src.retrieval.router import classify_query

RRF_K = 60   # costante RRF standard

# Una funzione di ricerca restituisce chiavi pagina ORDINATE per pertinenza.
PageKey = tuple[str, int]
SearchFn = Callable[[str, Role], Sequence[PageKey]]

TEXT = "text"
VISUAL = "visual"


@dataclass(frozen=True)
class FusedHit:
    """Pagina fusa: score RRF + da quali segnali proviene (audit)."""

    doc: str
    page: int
    score: float
    sources: tuple[str, ...]   # ("text",) | ("visual",) | ("text", "visual")


def _dedup(keys: Sequence[PageKey]) -> list[PageKey]:
    """Tiene solo la PRIMA occorrenza di ogni chiave (rank migliore) in una lista."""
    seen: set[PageKey] = set()
    out: list[PageKey] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def rrf_fuse(ranked: dict[str, Sequence[PageKey]], *, rrf_k: int = RRF_K) -> list[FusedHit]:
    """Fonde piu' liste ordinate di chiavi `(doc, page)` con Reciprocal Rank Fusion.

    score(chiave) = somma su ogni lista che la contiene di `1 / (rrf_k + rank)`.
    L'assenza da una lista = zero contributo da quella lista (penalita' corretta).
    Ordina per score decrescente (tie-break stabile su doc, page).
    """
    scores: dict[PageKey, float] = {}
    sources: dict[PageKey, list[str]] = {}
    for name, keys in ranked.items():
        for rank, key in enumerate(_dedup(keys), start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (rrf_k + rank)
            sources.setdefault(key, []).append(name)

    fused = [FusedHit(doc=doc, page=page, score=scores[(doc, page)],
                      sources=tuple(sources[(doc, page)]))
             for (doc, page) in scores]
    fused.sort(key=lambda f: (-f.score, f.doc, f.page))
    return fused


def hybrid_search(query: str, *, role: Role, text_search: SearchFn,
                  visual_search: SearchFn, k: int = 10, rrf_k: int = RRF_K,
                  classify: Callable[[str], object] = classify_query) -> list[FusedHit]:
    """Instrada la query e restituisce le pagine, fondendo i segnali se `mixed`.

    text_search/visual_search: funzioni `(query, role) -> lista ordinata di (doc,page)`
    (adattano i retriever reali; iniettabili nei test). `role` propagato a entrambi
    per il filtro RBAC. Ritorna al piu' `k` `FusedHit`.
    """
    query_type = getattr(classify(query), "query_type", "natural")

    if query_type == "code":
        fused = rrf_fuse({TEXT: text_search(query, role)}, rrf_k=rrf_k)
    elif query_type == "mixed":
        fused = rrf_fuse({TEXT: text_search(query, role),
                          VISUAL: visual_search(query, role)}, rrf_k=rrf_k)
    else:  # natural | visual -> PixelRAG puro
        fused = rrf_fuse({VISUAL: visual_search(query, role)}, rrf_k=rrf_k)

    return fused[:k]
