"""Fase 1.1 — Port del reranker visivo + adapter Qwen3-VL-Reranker.

Come l'embedder, anche il reranker e' un Port (Protocol `VisualReranker`) con
un'implementazione `Qwen3VLReranker` che nasconde il backend di inferenza 
(sentence-transformers) e gestisce l'hook al Multiplexer.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable
from src.retrieval.pixelrag.index import PageHit

@runtime_checkable
class VisualReranker(Protocol):
    """Contratto del reranker visivo, indipendente dal backend."""

    def rerank(self, query: str, pages: list[PageHit], top_k: int) -> list[PageHit]:
        """Riordina le candidate pages rispetto alla query, restituendo le top-k."""
        ...

class FakeVisualReranker:
    """Reranker DB-free in memoria per test."""
    
    def rerank(self, query: str, pages: list[PageHit], top_k: int) -> list[PageHit]:
        # Identita' ma limitata a top_k, preservando l'ordine
        return pages[:top_k]

class Qwen3VLReranker:
    """Adapter `VisualReranker` su Qwen3-VL-Reranker-2B."""

    DEFAULT_MODEL = "Qwen/Qwen3-VL-Reranker-2B"

    def __init__(self, multiplexer: object | None = None, model_name: str | None = None):
        self._multiplexer = multiplexer
        self.model_name = model_name or self.DEFAULT_MODEL
        self._model = None

    def _ensure_resident(self):
        """Hook Watchdog: chiede al Multiplexer di garantire la VRAM."""
        if self._multiplexer is not None:
            self._multiplexer.use(self.model_name)

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder
            # Qwen3-VL-Reranker richiede trust_remote_code
            self._model = CrossEncoder(self.model_name, trust_remote_code=True)
        return self._model

    def rerank(self, query: str, pages: list[PageHit], top_k: int) -> list[PageHit]:
        if not pages:
            return []
            
        self._ensure_resident()
        
        # We need the actual page renders!
        # The PageHit has `doc` and `page`. 
        # But wait! Does VisualReranker need to load the images?
        # Yes! Reranking happens on (query, image).
        from pathlib import Path
        from PIL import Image
        import logging
        
        ROOT = Path(__file__).resolve().parent.parent.parent.parent
        RENDERS = ROOT / "output" / "visual" / "renders"
        
        def get_page_image_path(doc: str, page: int) -> Path:
            return RENDERS / doc / f"p{page:04d}.png"

        
        pairs = []
        valid_pages = []
        for p in pages:
            img_path = get_page_image_path(p.doc, p.page)
            if img_path.exists():
                try:
                    img = Image.open(img_path).convert("RGB")
                    # sentence-transformers expects dict for multimodal inputs
                    pairs.append((query, {"image": img}))
                    valid_pages.append(p)
                except Exception as e:
                    logging.warning(f"Impossibile caricare {img_path}: {e}")
        
        if not pairs:
            return []
            
        model = self._get_model()
        scores = model.predict(pairs, prompt="Retrieve images or text relevant to the user's query.")
        
        # Update scores and sort
        reranked = []
        for page, score in zip(valid_pages, scores):
            # Crate a new PageHit with the updated score
            reranked.append(PageHit(doc=page.doc, page=page.page, score=float(score), n_tiles=page.n_tiles))
            
        reranked.sort(key=lambda p: (-p.score, p.doc, p.page))
        return reranked[:top_k]
