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
           query_filter: models.Filter | None = None,
           extra_terms: list[str] | None = None):
    """Ritorna la lista di ScoredPoint per la strategia richiesta.

    `query_filter` (P4.4, additivo) limita i CANDIDATI senza toccare lo
    scoring: nelle strategie ibride va dentro OGNI prefetch (e' li' che si
    generano i candidati), oltre che sull'outer query.

    `extra_terms` (debito §2, additivo): termini della Query Expansion,
    concatenati SOLO nel testo del canale sparse (BM25: bag-of-terms, la
    concatenazione E' l'OR) delle strategie rrf/routed-natural. Il canale
    dense resta sulla query originale; 'code'/'mixed'/sparse-diretta li
    IGNORANO (mai inquinare la ricerca esatta dei codici).
    """
    sparse_text = (f"{query} {' '.join(extra_terms)}" if extra_terms else query)
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
                models.Prefetch(query=_sparse_vec(sparse_text),
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
        # natural: unica route dove l'espansione entra (scope v1 debito §2)
        return search(client, query, "rrf", limit, query_filter=query_filter,
                      extra_terms=extra_terms)
    if strategy == "routed_rr":
        from src.retrieval.rerank import rerank, rerank_topk

        route = classify_query(query)
        if route.query_type == "code":
            return search(client, query, "routed", limit, query_filter=query_filter)
        base = search(client, query, "routed", limit=rerank_topk(),
                      query_filter=query_filter, extra_terms=extra_terms)
        # reranker sulla query ORIGINALE: l'espansione serve al recall dei
        # candidati, il punteggio resta sulla domanda vera dell'utente
        return rerank(query, base, limit=limit)
    raise ValueError(f"strategia sconosciuta: {strategy}")

def group_by_code(hits: list) -> list:
    """Hop 1: Deduplica risultati tenendo solo il primo (score max) per ogni codice."""
    seen = set()
    out = []
    for h in hits:
        c = (getattr(h, "payload", None) or {}).get("code")
        if c not in seen:
            out.append(h)
            if c:
                seen.add(c)
    return out

def apply_hop2(client: QdrantClient, hits: list, role: Role) -> tuple[list, list, str | None]:
    """Applica Hop 1 (group by) e Hop 2 (recupero children).

    P4.5/A6 — il recupero dei figli di un assembly applica lo STESSO filtro
    ruolo del resto del retrieval (`ruoli_consentiti` contiene il ruolo), in
    AND con `parent_id`: un ruolo non deve mai ricevere componenti riservati a
    un altro ruolo (l'RBAC è obbligatorio, non opzionale).
    """
    grouped_hits = group_by_code(hits)
    children_hits = []
    hop2_warning = None
    if grouped_hits and grouped_hits[0].payload.get("is_assembly"):
        kit_code = grouped_hits[0].payload.get("code")
        if kit_code:
            children_filter = models.Filter(must=[
                models.FieldCondition(key="parent_id",
                                      match=models.MatchValue(value=kit_code)),
                models.FieldCondition(key="ruoli_consentiti",
                                      match=models.MatchValue(value=role.value)),
            ])
            # Hop 2 exact match
            records, _ = client.scroll(COLLECTION, scroll_filter=children_filter, limit=100)
            if not records:
                hop2_warning = "Assembly_Has_No_Children"
            else:
                for r in records:
                    children_hits.append(models.ScoredPoint(
                        id=r.id, version=r.version, score=grouped_hits[0].score, payload=r.payload, vector=None
                    ))
    return grouped_hits, children_hits, hop2_warning


def search_unified(client: QdrantClient, query: str, limit: int = 10, *,
                   role: Role) -> dict:
    """Instradamento unificato testo/visione (P2.7).

    - query 'visual' -> collection visiva (pagine citabili con render);
      se porta anche un codice -> IN AGGIUNTA il canale lessicale su
      aike_chunks (l'esploso E la riga ricambio);
    - tutto il resto -> strategia routed su aike_chunks (invariata).
    Ritorna {"route", "reason", "text_hits", "visual_hits"}.

    P4.5/A6 — `role` è obbligatorio (keyword-only, fail-closed) e viene
    propagato fino all'Hop 2 per il filtro RBAC sui figli.
    """
    route = classify_query(query)
    if route.query_type == "visual":
        from src.retrieval.pixelrag.search import visual_search
        from src.retrieval.pixelrag.index import QdrantVisualIndex
        from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder
        from src.retrieval.pixelrag.reranker import Qwen3VLReranker
        from src.serving.multiplexer import ModelMultiplexer, FakeModelRuntime
        
        # Imposta max_resident=2 affinche' la coppia visiva embedder+reranker
        # coesista senza evincersi a vicenda. FakeModelRuntime per l'adapter Qwen.
        mux = ModelMultiplexer(runtime=FakeModelRuntime(), max_resident=2)
        embedder = Qwen3VLEmbedder(multiplexer=mux)
        reranker = Qwen3VLReranker(multiplexer=mux)
        index = QdrantVisualIndex(client)
        
        visual = visual_search(query, embedder=embedder, index=index, role=role, k=limit, reranker=reranker)
        
        text = (search(client, route.code_token, "sparse", limit)
                if route.code_token else [])
        text, children, hop2_warning = apply_hop2(client, text, role)
        return {"route": "visual", "reason": route.reason,
                "text_hits": text, "children_hits": children, "hop2_warning": hop2_warning,
                "visual_hits": visual}
    text, children, hop2_warning = apply_hop2(client, search(client, query, "routed", limit), role)
    return {"route": route.query_type, "reason": route.reason,
            "text_hits": text, "children_hits": children, "hop2_warning": hop2_warning,
            "visual_hits": []}
