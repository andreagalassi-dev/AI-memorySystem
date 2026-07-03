"""P2.7 — Encoder visivo ColQwen2 (patch-level) e ricerca MaxSim su Qdrant.

Collection separata `aike_pages_visual`: un punto = una PAGINA (multivettore
~700-800 patch x 128 dim, comparator MaxSim). HNSW disabilitato (m=0): a 878
punti la scansione brute-force e' rapida e azzera il costo RAM di build
dell'indice multivettore. Loader lazy come gli altri encoder del progetto.
"""

from __future__ import annotations

from functools import lru_cache

from qdrant_client import QdrantClient, models

VISUAL_COLLECTION = "aike_pages_visual"
# Porting NATIVO transformers (>=5.x) di ColQwen2: l'adapter LoRA del checkpoint
# colpali-engine 'vidore/colqwen2-v1.0' NON si carica con transformers 5.x
# (rinomina moduli Qwen2VL -> pesi lora "newly initialized" IN SILENZIO:
# embedding random, pagine bianche come attrattori — bug trovato dall'eval
# visiva e provato col test bianca-vs-target). Il checkpoint -hf e' la
# versione mantenuta dentro transformers: niente PEFT, niente version-fight.
VISUAL_MODEL = "vidore/colqwen2-v1.0-hf"
PATCH_DIM = 128
RENDER_DPI = 150
MAX_SIDE_PX = 1540   # cap risoluzione: tiene i patch <= ~800 (attenzione quadratica)


@lru_cache(maxsize=1)
def _model_and_processor():
    import torch
    from transformers import ColQwen2ForRetrieval, ColQwen2Processor

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = ColQwen2ForRetrieval.from_pretrained(
        VISUAL_MODEL, dtype=torch.bfloat16).to(device).eval()
    processor = ColQwen2Processor.from_pretrained(VISUAL_MODEL)
    return model, processor, device


def embed_pages(images) -> list[list[list[float]]]:
    """Multivettori patch-level per una lista di PIL.Image (batch piccoli!)."""
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


def ensure_visual_collection(client: QdrantClient, recreate: bool = False) -> None:
    if recreate and client.collection_exists(VISUAL_COLLECTION):
        client.delete_collection(VISUAL_COLLECTION)
    if not client.collection_exists(VISUAL_COLLECTION):
        client.create_collection(
            collection_name=VISUAL_COLLECTION,
            vectors_config=models.VectorParams(
                size=PATCH_DIM,
                # DOT come lo scorer ufficiale ColPali: la norma dei patch e'
                # informativa (patch vuoti = norma piccola), la normalizzazione
                # cosine li amplificherebbe
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
    from src.backend.clients.qdrant_client import get_qdrant_client

    client = get_qdrant_client()
    docs, offset = set(), None
    while True:
        pts, offset = client.scroll(VISUAL_COLLECTION, limit=512, offset=offset,
                                    with_payload=["doc"])
        docs.update(p.payload["doc"] for p in pts)
        if offset is None:
            return tuple(sorted(docs))


def doc_hint_filter(query: str) -> models.Filter | None:
    """Filtro deterministico: token della query che matchano il NOME documento.

    Il retrieval visivo e' ancorato al testo VISIBILE sulla pagina: gli esplosi
    muti non contengono il brand, che pero' sta nel nome del documento — e il
    tecnico la macchina la nomina ("...della pompa Iwaki"). Token alfabetici
    >=4 char confrontati con gli stem dei doc indicizzati; nessun match ->
    nessun filtro (ricerca globale).
    """
    tokens = {t.lower() for t in query.replace("-", " ").split()
              if len(t) >= 4 and t.isalpha()}
    matched = [d for d in _indexed_docs_cache()
               if any(tok in d.lower() for tok in tokens)]
    if not matched:
        return None
    return models.Filter(must=[models.FieldCondition(
        key="doc", match=models.MatchAny(any=matched))])


def search_visual(client: QdrantClient, query: str, limit: int = 10):
    """Ricerca MaxSim (+ doc-hint): pagine citabili (doc+pagina+render)."""
    return client.query_points(
        VISUAL_COLLECTION, query=embed_query(query),
        query_filter=doc_hint_filter(query),
        with_payload=True, limit=limit).points
