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
                  tile_k: int | None = None,
                  reranker=None) -> list[PageHit]:
    """Cerca le pagine visivamente piu' pertinenti alla query, filtrate per ruolo.

    Args:
      query: testo della domanda ("mostrami l'esploso del gruppo pompa").
      embedder: Port dell'embedder visivo (Qwen3-VL in prod, fake nei test).
      index: Port del motore vettoriale (Qdrant in prod, FakeVisualIndex nei test).
      role: ruolo RBAC del richiedente — OBBLIGATORIO, fail-closed (niente default).
      k: numero di PAGINE da restituire.
      version_status: stato versione ammesso (default 'current').
      tile_k: quanti TILE recuperare prima della ricomposizione (default k*OVERFETCH).
      reranker: opzionale Port del reranker visivo (Qwen3VLReranker in prod).

    Ritorna: pagine `PageHit` ordinate per score (MaxSim dei tile o score del reranker).
    """
    if not isinstance(role, Role):  # fail-closed: mai una query senza ruolo valido
        raise TypeError(f"role deve essere Role, non {type(role).__name__}")

    query_vec = embedder.embed_query(query)
    flt = access_filter(role, version_status) if role else None
    
    # Se c'e' un reranker, la pipeline recupera piu' tile e ne estrae 60 candidati
    if reranker is not None:
        eff_tile_k = 300
        eff_page_k = 60
    else:
        eff_tile_k = tile_k or k * OVERFETCH
        eff_page_k = k
        
    hits = index.search(query_vec, eff_tile_k, flt=flt, query_text=query)
    
    # Variante EN (Query Expansion deterministica)
    from src.retrieval.expansion import expand_query
    en_terms = expand_query(query, target_lang="EN")
    if en_terms:
        # TODO(debt): Fallback futuro LLM (Gemma) per i termini fuori dizionario.
        # Per ora costruiamo una variante EN accodando i termini trovati.
        en_query = query + " " + " ".join(en_terms)
        query_vec_en = embedder.embed_query(en_query)
        hits_en = index.search(query_vec_en, eff_tile_k, flt=flt, query_text=en_query)
        
        # Merge con max score
        merged_hits = {}
        for h in hits + hits_en:
            key = h.point_id
            if key not in merged_hits or h.score > merged_hits[key].score:
                merged_hits[key] = h
        hits = sorted(merged_hits.values(), key=lambda x: x.score, reverse=True)

    candidate_pages = pages_from_tiles(hits)[:eff_page_k]
    
    if reranker is not None:
        return reranker.rerank(query, candidate_pages, k)
        
    return candidate_pages
