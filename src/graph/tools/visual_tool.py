"""Fase 1.3 ramo VISUAL — puntatore deterministico alla pagina (NESSUN LLM).

Un match visivo e' una similarita' (MaxSim), non un lookup esatto: descrivere
l'immagine con un VLM rischierebbe l'allucinazione (rondelle/guarnizioni inventate
su un esploso CAD). Scelta Fail-Closed, coerente col principio del progetto: NON
facciamo generare testo a un modello: restituiamo un PUNTATORE deterministico alle
pagine (doc + pagina) e lasciamo che sia l'occhio umano a validare l'immagine.

Il ramo riusa integralmente la pipeline PixelRAG 1.1 (`visual_search`), che applica
RBAC + versioning **fail-closed** dietro i Port `VisualEmbedder`/`VisualIndex`; qui
aggiungiamo solo la formattazione e il binder di produzione.

La stringa di `format_visual_answer` e' STRUTTURALMENTE PREVEDIBILE: usa il pattern
di citazione canonico `(doc, pag. N)` (lo stesso di `synthesis`), cosi' il frontend
lo parsa con la stessa regex per iniettare a schermo l'immagine, e il guardrail P3.1
(che legge le affermazioni via `strip_citations`) resta un PASS.
"""

from __future__ import annotations

from typing import Callable, Sequence

from src.common.roles import Role
from src.retrieval.pixelrag.embedder import VisualEmbedder
from src.retrieval.pixelrag.index import PageHit, VisualIndex
from src.retrieval.pixelrag.search import visual_search
from src.retrieval.synthesis import REFUSAL

VISUAL_K = 3   # poche pagine, alta precisione: e' l'occhio umano a scegliere
VISUAL_INTRO = "Documentazione visiva pertinente (conferma visiva consigliata):"


def format_visual_answer(pages: Sequence[PageHit]) -> str:
    """Puntatore deterministico alle pagine, in forma parsabile dal frontend.

    Ogni pagina e' una citazione `(doc, pag. N)`: il frontend estrae doc_stem+page
    con la regex di `synthesis` e renderizza l'immagine. Su lista vuota -> REFUSAL
    (difesa: il nodo gia' degrada a BASSA su 0 pagine, ma non produciamo mai un
    puntatore a nulla)."""
    if not pages:
        return REFUSAL
    cites = "; ".join(f"({p.doc}, pag. {p.page})" for p in pages)
    return f"{VISUAL_INTRO} {cites}."


def visual_citations(pages: Sequence[PageHit]) -> list[tuple[str, int]]:
    """Citazioni (doc, pagina) dai `PageHit` — dalla PROVENIENZA, non da un LLM."""
    return [(p.doc, p.page) for p in pages]


def make_visual_search(embedder: VisualEmbedder, index: VisualIndex, *,
                       k: int = VISUAL_K,
                       reranker=None) -> Callable[[str, Role], list[PageHit]]:
    """Binder di produzione: chiude embedder+index su `visual_search`.

    Ritorna un callable `(query, role) -> list[PageHit]` (stessa firma usata da
    `hybrid`), da iniettare in `Deps.visual_search`. L'RBAC/versioning resta dentro
    `visual_search` (fail-closed: `role` obbligatorio, filtro d'indice).

    Se `reranker` e' fornito, viene inoltrato a `visual_search` (rerankizza solo le
    pagine gia' filtrate dall'RBAC: mai un aggiramento dei filtri ruolo/versione)."""
    def _search(query: str, role: Role) -> list[PageHit]:
        return visual_search(query, embedder=embedder, index=index, role=role, k=k,
                             reranker=reranker)

    return _search
