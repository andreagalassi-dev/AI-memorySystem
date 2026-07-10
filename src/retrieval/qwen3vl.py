"""Fase 1.1 — Backend torch/transformers per l'embedder visivo Qwen3-VL.

Backend unificato per PixelRAG: embedding visivi (patch-level multivettore) +
gestione collection Qdrant + ricerca MaxSim. Sostituisce completamente il vecchio
`src/retrieval/visual.py` (ColQwen2, P2.7, rimosso nella migrazione a Qwen3-VL).

Caricamento lazy (`lru_cache`): costruire l'adapter (e testarlo con fake) non
importa mai torch; il backend pesante si tocca solo alla prima chiamata reale,
dopo che il ModelMultiplexer ha garantito lo spazio RAM/VRAM (eviction dei
modelli inattivi).

Il checkpoint Qwen3-VL-Embedding e' **parametrico e spike-pending** (vedi
PIANO-IMPLEMENTAZIONE riga 18 + §Fase 1.1). Cambiare checkpoint = cambiare
`QWEN3VL_MODEL`, zero impatto sul resto.
"""

from __future__ import annotations

from functools import lru_cache

# Checkpoint parametrico: placeholder, NON hardcodato. Lo spike P1 conferma
# l'ID HuggingFace reale di "Qwen3-VL-Embedding" e la classe retrieval.
# Tenuto qui (non nel costruttore dell'adapter) perche' e' una costante del
# backend di inferenza, come VISUAL_MODEL in visual.py.
QWEN3VL_MODEL = "qwen-vl-embedding"
PATCH_DIM = 128
RENDER_DPI = 150
MAX_SIDE_PX = 1540


@lru_cache(maxsize=1)
def _model_and_processor():
    """Carica modello + processor torch (lazy). Spike-pending sul checkpoint.

    Speculare a `visual._model_and_processor`: device MPS su Apple Silicon,
    dtype bfloat16. La classe retrieval-specializzata sara' determinata dal
    checkpoint confermato allo spike (oggi si evita di bindare una classe
    non verificata: il SEAM e' questo, il binding e' del gate P1).
    """
    import torch
    from transformers import AutoModel, AutoProcessor

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = AutoModel.from_pretrained(QWEN3VL_MODEL, trust_remote_code=True,
                                     torch_dtype=torch.bfloat16).to(device).eval()
    processor = AutoProcessor.from_pretrained(QWEN3VL_MODEL, trust_remote_code=True)
    return model, processor, device


def embed_pages(images) -> list[list[list[float]]]:
    """Multivettori patch-level per una lista di PIL.Image (batch piccoli!).

    Stesso contratto di `visual.embed_pages`: lista di immagini -> lista
    (una per immagine) di liste di patch-vettori (128-d). Il downstream
    (QdrantVisualIndex) e' identico al path del predecessore.
    """
    import torch

    model, processor, device = _model_and_processor()
    batch = processor.process_images(images).to(device)
    with torch.no_grad():
        out = model(**batch).embeddings
    return [e.to(torch.float32).cpu().numpy().tolist() for e in out]


def embed_query(text: str) -> list[list[float]]:
    """Multivettore della query testuale (stesso spazio patch/token)."""
    import torch

    model, processor, device = _model_and_processor()
    batch = processor.process_queries([text]).to(device)
    with torch.no_grad():
        out = model(**batch).embeddings
    return out[0].to(torch.float32).cpu().numpy().tolist()


# ----------------------------------------------------------- Qdrant collection

VISUAL_COLLECTION = "aike_pages_visual"


def ensure_visual_collection(client, recreate: bool = False) -> None:
    """Crea (o ricrea) la collection visiva multivettore su Qdrant.

    Un punto = una PAGINA (multivettore ~700-800 patch x 128 dim, comparator
    MaxSim). HNSW disabilitato (m=0): a questo volume la scansione brute-force
    e' rapida e azzera il costo RAM di build dell'indice multivettore.
    """
    from qdrant_client import models

    if recreate and client.collection_exists(VISUAL_COLLECTION):
        client.delete_collection(VISUAL_COLLECTION)
    if not client.collection_exists(VISUAL_COLLECTION):
        client.create_collection(
            collection_name=VISUAL_COLLECTION,
            vectors_config=models.VectorParams(
                size=PATCH_DIM,
                # DOT come scorer ufficiale: la norma dei patch e' informativa
                # (patch vuoti = norma piccola); la normalizzazione cosine li
                # amplificherebbe
                distance=models.Distance.DOT,
                multivector_config=models.MultiVectorConfig(
                    comparator=models.MultiVectorComparator.MAX_SIM),
                hnsw_config=models.HnswConfigDiff(m=0),  # brute-force MaxSim
            ),
        )
        for f in ("doc", "cat", "page"):
            client.create_payload_index(
                VISUAL_COLLECTION, field_name=f,
                field_schema=models.PayloadSchemaType.INTEGER if f == "page"
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
    """Filtro deterministico: token della query che matchano il NOME documento.

    Il retrieval visivo e' ancorato al testo VISIBILE sulla pagina: gli esplosi
    muti non contengono il brand, che pero' sta nel nome del documento — e il
    tecnico la macchina la nomina ("...della pompa Iwaki"). Token alfabetici
    >=4 char confrontati con gli stem dei doc indicizzati; nessun match ->
    nessun filtro (ricerca globale).
    """
    from qdrant_client import models

    tokens = {t.lower() for t in query.replace("-", " ").split()
               if len(t) >= 4 and t.isalpha()}
    matched = [d for d in _indexed_docs_cache()
               if any(tok in d.lower() for tok in tokens)]
    if not matched:
        return None
    return models.Filter(must=[models.FieldCondition(
        key="doc", match=models.MatchAny(any=matched))])


def search_visual(client, query: str, limit: int = 10):
    """Ricerca MaxSim (+ doc-hint): pagine citabili (doc+pagina+render)."""
    return client.query_points(
        VISUAL_COLLECTION, query=embed_query(query),
        query_filter=doc_hint_filter(query),
        with_payload=True, limit=limit).points
