"""P2.4 — Strategie di ricerca su Qdrant (dense / sparse / rrf / routed).

Riusa gli STESSI embedder e la stessa text-prep dell'ingestion
(`src/ingestion/embedding.py`): qualsiasi asimmetria indice/query falserebbe
le misure. `routed` applica il router deterministico: query 'code' ->
sparse-only; 'mixed'/'natural' -> ibrida RRF.
"""

from __future__ import annotations

from qdrant_client import QdrantClient, models

from src.ingestion.embedding import (
    COLLECTION,
    DENSE_NAME,
    SPARSE_NAME,
    embed_dense,
    embed_sparse_query,
)
from src.retrieval.router import classify_query

STRATEGIES = ("dense", "sparse", "rrf", "routed")
# routed_rr (P2.8d, ADOTTATA): routed + cross-encoder BGE-reranker-v2-m3 sui
# top-20, SOLO per query non-code (misurato: R@1 +0.115, mixed 0.50->0.875,
# latenza mediana 0.76s dichiarata). E' la strategia di produzione per P3.


def _sparse_vec(query: str) -> models.SparseVector:
    q = embed_sparse_query(query)
    return models.SparseVector(indices=q.indices.tolist(), values=q.values.tolist())


def search(client: QdrantClient, query: str, strategy: str, limit: int = 10):
    """Ritorna la lista di ScoredPoint per la strategia richiesta."""
    if strategy == "sparse":
        return client.query_points(
            COLLECTION, query=_sparse_vec(query), using=SPARSE_NAME,
            with_payload=True, limit=limit).points
    if strategy == "dense":
        return client.query_points(
            COLLECTION, query=embed_dense([query])[0], using=DENSE_NAME,
            with_payload=True, limit=limit).points
    if strategy == "rrf":
        return client.query_points(
            COLLECTION,
            prefetch=[
                models.Prefetch(query=embed_dense([query])[0],
                                using=DENSE_NAME, limit=limit * 2),
                models.Prefetch(query=_sparse_vec(query),
                                using=SPARSE_NAME, limit=limit * 2),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            with_payload=True, limit=limit).points
    if strategy == "routed":
        route = classify_query(query)
        if route.query_type == "code":
            # SOLO il token-codice al canale lessicale: le parole-marker
            # ("part number", "codice") matcherebbero migliaia di chunk-testo
            # sommergendo il token unico (misurato in P2.6: 19.9 vs 15.9).
            return search(client, route.code_token, "sparse", limit)
        if route.query_type == "mixed":
            # ibrida: lessicale sul token-codice, semantica sulla frase intera
            return client.query_points(
                COLLECTION,
                prefetch=[
                    models.Prefetch(query=embed_dense([query])[0],
                                    using=DENSE_NAME, limit=limit * 2),
                    models.Prefetch(query=_sparse_vec(route.code_token),
                                    using=SPARSE_NAME, limit=limit * 2),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                with_payload=True, limit=limit).points
        return search(client, query, "rrf", limit)
    if strategy == "routed_rr":
        from src.retrieval.rerank import RERANK_TOPK, rerank

        route = classify_query(query)
        if route.query_type == "code":
            return search(client, query, "routed", limit)
        base = search(client, query, "routed", limit=RERANK_TOPK)
        return rerank(query, base, limit=limit)
    raise ValueError(f"strategia sconosciuta: {strategy}")


def search_unified(client: QdrantClient, query: str, limit: int = 10) -> dict:
    """Instradamento unificato testo/visione (P2.7).

    - query 'visual' -> collection visiva (pagine citabili con render);
      se porta anche un codice -> IN AGGIUNTA il canale lessicale su
      aike_chunks (l'esploso E la riga ricambio);
    - tutto il resto -> strategia routed su aike_chunks (invariata).
    Ritorna {"route", "reason", "text_hits", "visual_hits"}.
    """
    from src.retrieval.visual import search_visual

    route = classify_query(query)
    if route.query_type == "visual":
        visual = search_visual(client, query, limit)
        text = (search(client, route.code_token, "sparse", limit)
                if route.code_token else [])
        return {"route": "visual", "reason": route.reason,
                "text_hits": text, "visual_hits": visual}
    return {"route": route.query_type, "reason": route.reason,
            "text_hits": search(client, query, "routed", limit),
            "visual_hits": []}
