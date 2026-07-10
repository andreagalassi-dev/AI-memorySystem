"""Fase 1.1 — Port dell'embedder visivo + adapter Qwen3-VL (maschera torch).

Il Protocol `VisualEmbedder` e' l'unica cosa che il resto di PixelRAG conosce:
`embed_tiles` (immagini -> vettori) ed `embed_query` (testo -> vettore). Le logiche
specifiche del backend di inferenza (PyTorch/MPS con Qwen3-VL) restano
COMPLETAMENTE dietro l'adapter: chi orchestra retrieval/ingest non importa mai torch.

`Qwen3VLEmbedder` e' l'adapter della Fase 1.1 su `src/retrieval/qwen3vl.py`
(checkpoint parametrico, spike-pending), con l'hook al `ModelMultiplexer`
(Watchdog): prima del primo caricamento reale chiede al Multiplexer di evictare
i modelli inattivi per garantire lo spazio RAM/VRAM (PIANO-IMPLEMENTAZIONE riga 6).

Le dipendenze sono INIETTABILI e risolte in modo LAZY: costruire l'adapter (e
testarlo con fake) non importa ne' `qwen3vl` ne' torch; il backend pesante si
tocca solo alla prima chiamata reale. Cambiare modello = nuovo adapter, zero
impatto sul resto.

Design: docs/superpowers/specs/2026-07-09-fase1.1-pixelrag-design.md.
"""

from __future__ import annotations

from typing import Callable, Protocol, Sequence, runtime_checkable

# Un embedding e' opaco al resto del sistema: single-vector oppure multivettore
# patch-level (Qwen3-VL). Il Port non vincola la forma.
Vector = Sequence[float] | Sequence[Sequence[float]]


@runtime_checkable
class VisualEmbedder(Protocol):
    """Contratto dell'embedder visivo, indipendente dal backend di inferenza."""

    def embed_tiles(self, images: Sequence) -> list[Vector]:
        ...

    def embed_query(self, text: str) -> Vector:
        ...


class Qwen3VLEmbedder:
    """Adapter `VisualEmbedder` su Qwen3-VL-Embedding (Fase 1.1), backend torch mascherato.

    Le funzioni di embedding sono iniettabili (per i test) e risolte lazy dai
    default nativi di `src.retrieval.qwen3vl`: nessun import di torch finche' non
    si estrae davvero. Cambiare modello = nuovo adapter, zero impatto sul resto
    di PixelRAG.

    Hook al `ModelMultiplexer` (PIANO-IMPLEMENTAZIONE riga 6, Watchdog): prima
    della prima chiamata reale chiama `multiplexer.use(model_name)`: il Watchdog
    evicta i modelli inattivi (LRU, es. Gemma 4) per garantire lo spazio
    RAM/VRAM prima che il VLM reclami memoria. Su Mac M1 Pro 32GB la memoria
    unificata e' condivisa: caricare Qwen3-VL insieme a Gemma 4 12B porterebbe
    a OOM senza questo hook.

    `use()` e' idempotente (anti-churn): richiedere un modello gia' residente
    non provoca reload. Il backend torch (`src.retrieval.qwen3vl`) e' il path
    di inferenza; l'invariante di memoria (eviction-before-load) e' del
    Multiplexer. Il binding del checkpoint Qwen3-VL e' spike-pending (gate P1).
    """

    DEFAULT_MODEL = "qwen-vl-embedding"

    def __init__(self, *, embed_pages: Callable | None = None,
                 embed_query: Callable | None = None,
                 multiplexer: object | None = None,
                 model_name: str | None = None):
        self._embed_pages = embed_pages
        self._embed_query = embed_query
        self._multiplexer = multiplexer
        self.model_name = model_name or self.DEFAULT_MODEL

    def _ensure_resident(self) -> None:
        """Hook Watchdog: chiede al Multiplexer di garantire la RAM/VRAM.

        `multiplexer.use(model_name)` evicta i modelli inattivi (LRU) se
        necessario, poi registra il VLM come residente. Idempotente. Se nessun
        multiplexer e' iniettato (es. test unitari o path legacy), no-op.
        """
        if self._multiplexer is not None:
            self._multiplexer.use(self.model_name)

    def _pages_fn(self) -> Callable:
        if self._embed_pages is None:
            from src.retrieval.qwen3vl import embed_pages  # torch lazy dentro
            self._embed_pages = embed_pages
        return self._embed_pages

    def _query_fn(self) -> Callable:
        if self._embed_query is None:
            from src.retrieval.qwen3vl import embed_query
            self._embed_query = embed_query
        return self._embed_query

    def embed_tiles(self, images: Sequence) -> list[Vector]:
        """Immagini dei tile (PIL) -> lista di embedding (uno per tile)."""
        imgs = list(images)
        if not imgs:
            return []
        self._ensure_resident()
        return list(self._pages_fn()(imgs))

    def embed_query(self, text: str) -> Vector:
        """Testo della query -> embedding nello stesso spazio dei tile."""
        self._ensure_resident()
        return self._query_fn()(text)
