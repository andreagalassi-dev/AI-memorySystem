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
from src.common.roles import Role
from src.retrieval.router import classify_query

STRATEGIES = ("dense", "sparse", "rrf", "routed")
# routed_rr (P2.8d, ADOTTATA): routed + cross-encoder BGE-reranker-v2-m3 sui
# top-20, SOLO per query non-code (misurato: R@1 +0.115, mixed 0.50->0.875,
# latenza mediana 0.76s dichiarata). E' la strategia di produzione per P3.


def _sparse_vec(query: str) -> models.SparseVector:
    q = embed_sparse_query(query)
    return models.SparseVector(indices=q.indices.tolist(), values=q.values.tolist())


def version_filter(status: str) -> models.Filter:
    """P4.4 — Filtro sul payload `version_status` (indicizzato in P4.2)."""
    return models.Filter(must=[models.FieldCondition(
        key="version_status", match=models.MatchValue(value=status))])


def access_filter(version_status: str, role: Role) -> models.Filter:
    """P4.5 — AND(versione, ruolo): MatchValue su `ruoli_consentiti` (lista
    keyword indicizzata in P4.2) = "l'array contiene il ruolo". Il tipo Role
    e' l'unico ammesso: la validazione a monte e' gia' avvenuta."""
    return models.Filter(must=[
        models.FieldCondition(key="version_status",
                              match=models.MatchValue(value=version_status)),
        models.FieldCondition(key="ruoli_consentiti",
                              match=models.MatchValue(value=role.value)),
    ])


def search(client: QdrantClient, query: str, strategy: str, limit: int = 10,
           query_filter: models.Filter | None = None):
    """Ritorna la lista di ScoredPoint per la strategia richiesta.

    `query_filter` (P4.4, additivo) limita i CANDIDATI senza toccare lo
    scoring: nelle strategie ibride va dentro OGNI prefetch (e' li' che si
    generano i candidati), oltre che sull'outer query.
    """
    if strategy == "sparse":
        return client.query_points(
            COLLECTION, query=_sparse_vec(query), using=SPARSE_NAME,
            query_filter=query_filter, with_payload=True, limit=limit).points
    if strategy == "dense":
        return client.query_points(
            COLLECTION, query=embed_dense([query])[0], using=DENSE_NAME,
            query_filter=query_filter, with_payload=True, limit=limit).points
    if strategy == "rrf":
        return client.query_points(
            COLLECTION,
            prefetch=[
                models.Prefetch(query=embed_dense([query])[0],
                                using=DENSE_NAME, limit=limit * 2,
                                filter=query_filter),
                models.Prefetch(query=_sparse_vec(query),
                                using=SPARSE_NAME, limit=limit * 2,
                                filter=query_filter),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            query_filter=query_filter, with_payload=True, limit=limit).points
    if strategy == "routed":
        route = classify_query(query)
        if route.query_type == "code":
            # SOLO il token-codice al canale lessicale: le parole-marker
            # ("part number", "codice") matcherebbero migliaia di chunk-testo
            # sommergendo il token unico (misurato in P2.6: 19.9 vs 15.9).
            return search(client, route.code_token, "sparse", limit,
                          query_filter=query_filter)
        if route.query_type == "mixed":
            # ibrida: lessicale sul token-codice, semantica sulla frase intera
            return client.query_points(
                COLLECTION,
                prefetch=[
                    models.Prefetch(query=embed_dense([query])[0],
                                    using=DENSE_NAME, limit=limit * 2,
                                    filter=query_filter),
                    models.Prefetch(query=_sparse_vec(route.code_token),
                                    using=SPARSE_NAME, limit=limit * 2,
                                    filter=query_filter),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),
                query_filter=query_filter, with_payload=True, limit=limit).points
        return search(client, query, "rrf", limit, query_filter=query_filter)
    if strategy == "routed_rr":
        from src.retrieval.rerank import RERANK_TOPK, rerank

        route = classify_query(query)
        if route.query_type == "code":
            return search(client, query, "routed", limit, query_filter=query_filter)
        base = search(client, query, "routed", limit=RERANK_TOPK,
                      query_filter=query_filter)
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
