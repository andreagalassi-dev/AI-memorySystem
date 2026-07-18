"""Encoder visivo ColQwen3 (patch-level) e ricerca MaxSim su Qdrant.

Collection separata `aike_pages_visual_colqwen3`: un punto = una PAGINA (multivettore
~1271 patch x 320 dim, comparator MaxSim). HNSW disabilitato (m=0): a 1035
punti la scansione brute-force e' rapida.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from qdrant_client import QdrantClient, models

VISUAL_COLLECTION_COLQWEN3 = "aike_pages_visual_colqwen3"
VISUAL_MODEL = "TomoroAI/tomoro-colqwen3-embed-4b"
REVISION = "bf790bd8780b098b86453444632a184bb770be1a"
PATCH_DIM = 320
RENDER_DPI = 150
MAX_SIDE_PX = 1540


@lru_cache(maxsize=1)
def _model_and_processor():
    import torch
    import sys
    ROOT = Path(__file__).resolve().parent.parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
        
    from src.vendor.colqwen3_tomoro.modeling_colqwen3 import ColQwen3
    from src.vendor.colqwen3_tomoro.processing_colqwen3 import ColQwen3Processor

    device = "mps" if torch.backends.mps.is_available() else "cpu"
    model = ColQwen3.from_pretrained(
        VISUAL_MODEL, 
        revision=REVISION,
        attn_implementation="sdpa",
        torch_dtype=torch.bfloat16,
        trust_remote_code=False,
    ).to(device).eval()
    processor = ColQwen3Processor.from_pretrained(
        VISUAL_MODEL,
        revision=REVISION,
        trust_remote_code=False,
    )
    return model, processor, device


def embed_pages(images) -> list[list[list[float]]]:
    """Multivettori patch-level per una lista di PIL.Image (batch piccoli!)."""
    import torch

    model, processor, device = _model_and_processor()
    if hasattr(processor, "process_images"):
        batch = processor.process_images(images).to(device)
    else:
        batch = processor(images=images, return_tensors="pt", padding=True).to(device)
        
    with torch.no_grad():
        out = model(**batch).embeddings
    return [e.to(torch.float32).cpu().numpy().tolist() for e in out]


def embed_query(text: str) -> list[list[float]]:
    """Multivettore della query testuale (stesso spazio patch/token)."""
    import torch

    model, processor, device = _model_and_processor()
    if hasattr(processor, "process_queries"):
        batch = processor.process_queries([text]).to(device)
    else:
        batch = processor(text=[text], return_tensors="pt", padding=True).to(device)
        
    with torch.no_grad():
        out = model(**batch).embeddings
    return out[0].to(torch.float32).cpu().numpy().tolist()


def ensure_visual_collection_colqwen3(client: QdrantClient, recreate: bool = False) -> None:
    if recreate and client.collection_exists(VISUAL_COLLECTION_COLQWEN3):
        client.delete_collection(VISUAL_COLLECTION_COLQWEN3)
    if not client.collection_exists(VISUAL_COLLECTION_COLQWEN3):
        client.create_collection(
            collection_name=VISUAL_COLLECTION_COLQWEN3,
            vectors_config=models.VectorParams(
                size=PATCH_DIM,
                distance=models.Distance.DOT,
                multivector_config=models.MultiVectorConfig(
                    comparator=models.MultiVectorComparator.MAX_SIM),
                hnsw_config=models.HnswConfigDiff(m=0),  # brute-force MaxSim
            ),
        )
        for f in ("doc", "cat", "page"):
            client.create_payload_index(
                VISUAL_COLLECTION_COLQWEN3, field_name=f,
                field_schema=models.PayloadSchemaType.INTEGER if f == "page"
                else models.PayloadSchemaType.KEYWORD)


@lru_cache(maxsize=1)
def _indexed_docs_cache() -> tuple[str, ...]:
    from src.backend.clients.qdrant_client import get_qdrant_client

    client = get_qdrant_client()
    docs, offset = set(), None
    while True:
        pts, offset = client.scroll(VISUAL_COLLECTION_COLQWEN3, limit=512, offset=offset,
                                    with_payload=["doc"])
        docs.update(p.payload["doc"] for p in pts)
        if offset is None:
            return tuple(sorted(docs))


def doc_hint_filter(query: str) -> models.Filter | None:
    tokens = {t.lower() for t in query.replace("-", " ").split()
              if len(t) >= 4 and t.isalpha()}
    matched = [d for d in _indexed_docs_cache()
               if any(tok in d.lower() for tok in tokens)]
    if not matched:
        return None
    return models.Filter(must=[models.FieldCondition(
        key="doc", match=models.MatchAny(any=matched))])


def search_visual_colqwen3(client: QdrantClient, query: str, limit: int = 10):
    return client.query_points(
        VISUAL_COLLECTION_COLQWEN3, query=embed_query(query),
        query_filter=doc_hint_filter(query),
        with_payload=True, limit=limit).points
