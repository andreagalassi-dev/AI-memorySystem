"""Fase 1.1 — Backend sentence-transformers per l'embedder visivo Qwen3-VL.

Backend unificato per PixelRAG: embedding visivi (single-vector MRL) +
gestione collection Qdrant + ricerca Cosine. Sostituisce completamente il vecchio
`src/retrieval/visual.py` e il paradigma multivettore MaxSim.

Caricamento lazy (`lru_cache`): costruire l'adapter (e testarlo con fake) non
importa mai torch/sentence_transformers; il backend pesante si tocca solo alla
prima chiamata reale, dopo che il ModelMultiplexer ha garantito lo spazio RAM/VRAM
(eviction dei modelli inattivi).
"""

from __future__ import annotations

from functools import lru_cache

QWEN3VL_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
EMBED_DIM = 1024  # MRL: tronca a 1024
RENDER_DPI = 150
MAX_SIDE_PX = 1540
QUERY_INSTRUCTION = "Given a visual query, find the corresponding image."


@lru_cache(maxsize=1)
def _model():
    """Carica modello sentence-transformers (lazy)."""
    from sentence_transformers import SentenceTransformer

    # trust_remote_code is necessary for Qwen3-VL models
    model = SentenceTransformer(QWEN3VL_MODEL, trust_remote_code=True)
    return model


def embed_pages(images) -> list[list[float]]:
    """Vettore singolo MRL-1024 per una lista di PIL.Image (batch piccoli!).

    Lista di immagini -> lista di vettori (1024-d).
    """
    import torch
    import torch.nn.functional as F

    model = _model()
    # convert_to_tensor to use PyTorch tensor operations
    embeddings = model.encode(images, convert_to_tensor=True)
    
    # MRL: tronca a 1024 e normalizza (L2) per distance=COSINE
    embeddings_trunc = embeddings[:, :EMBED_DIM]
    embeddings_norm = F.normalize(embeddings_trunc, p=2, dim=1)
    return embeddings_norm.to(torch.float32).cpu().numpy().tolist()


def embed_query(text: str) -> list[float]:
    """Vettore singolo della query testuale (MRL-1024) con instruction."""
    import torch
    import torch.nn.functional as F

    model = _model()
    # model is instruction-aware
    embeddings = model.encode([text], prompt_name="query", convert_to_tensor=True)
    
    # MRL: tronca a 1024 e normalizza
    embeddings_trunc = embeddings[:, :EMBED_DIM]
    embeddings_norm = F.normalize(embeddings_trunc, p=2, dim=1)
    return embeddings_norm[0].to(torch.float32).cpu().numpy().tolist()


# ----------------------------------------------------------- Qdrant collection

VISUAL_COLLECTION = "aike_pages_visual"


def ensure_visual_collection(client, recreate: bool = False) -> None:
    """Crea (o ricrea) la collection visiva single-vector su Qdrant.

    Un punto = un TILE. HNSW attivo, distance COSINE, size EMBED_DIM (1024).
    """
    from qdrant_client import models

    if recreate and client.collection_exists(VISUAL_COLLECTION):
        client.delete_collection(VISUAL_COLLECTION)
    if not client.collection_exists(VISUAL_COLLECTION):
        client.create_collection(
            collection_name=VISUAL_COLLECTION,
            vectors_config=models.VectorParams(
                size=EMBED_DIM,
                distance=models.Distance.COSINE,
            ),
        )
        for f in ("doc", "cat", "page", "tile"):
            client.create_payload_index(
                VISUAL_COLLECTION, field_name=f,
                field_schema=models.PayloadSchemaType.INTEGER if f in ("page", "tile")
                else models.PayloadSchemaType.KEYWORD)


@lru_cache(maxsize=1)
def _indexed_docs_cache() -> tuple[str, ...]:
    """Cache degli stem documenti indicizzati nella collection visiva."""
    from src.backend.clients.qdrant_client import get_qdrant_client

    client = get_qdrant_client()
    docs, offset = set(), None
    while True:
        pts, offset = client.scroll(VISUAL_COLLECTION, limit=512, offset=offset,
                                    with_payload=["doc"])
        docs.update(p.payload["doc"] for p in pts)
        if offset is None:
            return tuple(sorted(docs))


def doc_hint_filter(query: str):
    """Filtro deterministico: token della query che matchano il NOME documento."""
    from qdrant_client import models

    tokens = {t.lower() for t in query.replace("-", " ").split()
               if len(t) >= 3 and t.isalpha()}
    matched = [d for d in _indexed_docs_cache()
               if any(tok in d.lower() for tok in tokens)]
    if not matched:
        return None
    return models.Filter(must=[models.FieldCondition(
        key="doc", match=models.MatchAny(any=matched))])



