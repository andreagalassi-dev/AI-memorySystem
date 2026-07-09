"""Fase 1.1 — Ricerca visiva PixelRAG: query testuale -> pagine, filtrata per RBAC.

Cabla il flusso a livello Port (embedder + index, entrambi iniettati, quindi
testabile col Fake):

    query --embed_query--> vettore --index.search(flt RBAC)--> hit-tile
          --pages_from_tiles(MaxSim)--> pagine ordinate

RBAC/versioning **obbligatori e fail-closed**: il `role` e' un parametro richiesto
(mai un default che aprirebbe l'accesso) e diventa filtro sull'indice
(`ruoli_consentiti` deve contenere il ruolo); di default si consultano solo le
versioni `current` (SPEC §F.3, §E). Un commerciale non vede i tile di un tecnico.

Over-fetch dei tile: un punto = un tile, e piu' tile collassano sulla stessa pagina;
per restituire `k` PAGINE distinte si recuperano `k * OVERFETCH` TILE e poi si
ricompone. Design: docs/superpowers/specs/2026-07-09-fase1.1-pixelrag-design.md.
"""

from __future__ import annotations

from src.common.roles import Role
from src.retrieval.pixelrag.embedder import VisualEmbedder
from src.retrieval.pixelrag.index import PageHit, VisualIndex, pages_from_tiles

CURRENT = "current"
OVERFETCH = 5   # tile recuperati per pagina desiderata (i tile collassano su (doc,page))


def access_filter(role: Role, version_status: str = CURRENT) -> dict:
    """Filtro RBAC+versione per l'indice visivo (parita' con `search.access_filter`)."""
    return {"ruoli_consentiti": role, "version_status": version_status}


def visual_search(query: str, *, embedder: VisualEmbedder, index: VisualIndex,
                  role: Role, k: int = 10, version_status: str = CURRENT,
                  tile_k: int | None = None) -> list[PageHit]:
    """Cerca le pagine visivamente piu' pertinenti alla query, filtrate per ruolo.

    Args:
      query: testo della domanda ("mostrami l'esploso del gruppo pompa").
      embedder: Port dell'embedder visivo (ColQwen2 in prod, fake nei test).
      index: Port del motore vettoriale (Qdrant in prod, FakeVisualIndex nei test).
      role: ruolo RBAC del richiedente — OBBLIGATORIO, fail-closed (niente default).
      k: numero di PAGINE da restituire.
      version_status: stato versione ammesso (default 'current').
      tile_k: quanti TILE recuperare prima della ricomposizione (default k*OVERFETCH).

    Ritorna: pagine `PageHit` ordinate per score (MaxSim dei tile), al massimo `k`.
    """
    if not isinstance(role, Role):  # fail-closed: mai una query senza ruolo valido
        raise TypeError(f"role deve essere Role, non {type(role).__name__}")

    query_vec = embedder.embed_query(query)
    flt = access_filter(role, version_status)
    hits = index.search(query_vec, tile_k or k * OVERFETCH, flt=flt)
    return pages_from_tiles(hits)[:k]
