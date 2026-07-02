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
        inner = "sparse" if route.query_type == "code" else "rrf"
        return search(client, query, inner, limit)
    raise ValueError(f"strategia sconosciuta: {strategy}")
