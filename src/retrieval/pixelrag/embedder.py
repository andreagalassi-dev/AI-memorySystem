"""Fase 1.1 — Port dell'embedder visivo + adapter ColQwen2 (maschera torch/MLX).

Il Protocol `VisualEmbedder` e' l'unica cosa che il resto di PixelRAG conosce:
`embed_tiles` (immagini -> vettori) ed `embed_query` (testo -> vettore). Le logiche
specifiche del backend di inferenza (PyTorch/MPS oggi con ColQwen2, MLX/Qwen3-VL
domani) restano COMPLETAMENTE dietro l'adapter: chi orchestra retrieval/ingest non
importa mai torch.

`ColQwen2Embedder` avvolge le funzioni native gia' validate in P2.7
(`src/retrieval/visual.py`: `embed_pages`, `embed_query`). Le dipendenze sono
INIETTABILI e risolte in modo LAZY: costruire l'adapter (e testarlo con fake) non
importa ne' `visual` ne' torch; il backend pesante si tocca solo alla prima
chiamata reale. Cambiare modello = nuovo adapter, zero impatto sul resto.

Design: docs/superpowers/specs/2026-07-09-fase1.1-pixelrag-design.md.
"""

from __future__ import annotations

from typing import Callable, Protocol, Sequence, runtime_checkable

# Un embedding e' opaco al resto del sistema: single-vector oppure multivettore
# patch-level (ColQwen2). Il Port non vincola la forma.
Vector = Sequence[float] | Sequence[Sequence[float]]


@runtime_checkable
class VisualEmbedder(Protocol):
    """Contratto dell'embedder visivo, indipendente dal backend di inferenza."""

    def embed_tiles(self, images: Sequence) -> list[Vector]:
        ...

    def embed_query(self, text: str) -> Vector:
        ...


class ColQwen2Embedder:
    """Adapter `VisualEmbedder` su ColQwen2 (P2.7), backend torch/MPS mascherato.

    Le funzioni di embedding sono iniettabili (per i test) e risolte lazy dai
    default nativi di `src/retrieval/visual.py`: nessun import di torch finche' non
    si estrae davvero.
    """

    def __init__(self, *, embed_pages: Callable | None = None,
                 embed_query: Callable | None = None):
        self._embed_pages = embed_pages
        self._embed_query = embed_query

    def _pages_fn(self) -> Callable:
        if self._embed_pages is None:
            from src.retrieval.visual import embed_pages  # torch lazy dentro
            self._embed_pages = embed_pages
        return self._embed_pages

    def _query_fn(self) -> Callable:
        if self._embed_query is None:
            from src.retrieval.visual import embed_query
            self._embed_query = embed_query
        return self._embed_query

    def embed_tiles(self, images: Sequence) -> list[Vector]:
        """Immagini dei tile (PIL) -> lista di embedding (uno per tile)."""
        imgs = list(images)
        if not imgs:
            return []
        return list(self._pages_fn()(imgs))

    def embed_query(self, text: str) -> Vector:
        """Testo della query -> embedding nello stesso spazio dei tile."""
        return self._query_fn()(text)
