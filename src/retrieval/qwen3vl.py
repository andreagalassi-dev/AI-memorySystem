"""Fase 1.1 — Backend torch/transformers per l'embedder visivo Qwen3-VL.

Specularie a `src/retrieval/visual.py` (ColQwen2, P2.7): stesso pattern di
caricamento lazy (`lru_cache`), stesso comparator MaxSim su Qdrant, stesso
contratto `embed_pages`/`embed_query`. La differenza e' il checkpoint:
ColQwen2 e' verificato; Qwen3-VL-Embedding e' **parametrico e spike-pending**
(vedi PIANO-IMPLEMENTAZIONE riga 18 + §Fase 1.1).

IMPORTANT: il nome esatto del checkpoint HuggingFace e della classe
retrieval-specializzata (es. un `ColQwen3ForRetrieval` analogo) va confermato
allo spike di estrazione reale (Gate P1). Qui esponiamo il SEAM corretto:
caricamento torch/transformers su MPS, processor per immagini/query, output
multivettore patch-level nello stesso spazio di `embedder.embed_query`.
Cambiare checkpoint = cambiare `QWEN3VL_MODEL`, zero impatto sul resto.

L'embedder visivo (`Qwen3VLEmbedder`) risolve queste funzioni in modo LAZY:
costruire l'adapter (e testarlo con fake) non importa mai torch; il backend
pesante si tocca solo alla prima chiamata reale, dopo che il ModelMultiplexer
ha garantito lo spazio RAM/VRAM (eviction dei modelli inattivi).
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
    (QdrantVisualIndex) e' identico al path ColQwen2.
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
