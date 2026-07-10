"""Fase 1.1 — Orchestratore di ingestion visiva PixelRAG (pagina grafica -> Qdrant).

Chiude il binario "cerca": data una PDF con grafica/esplosi, per ogni pagina VISIVA
(classificata da P2.5) renderizza + taglia i tile, li embedda col Port
`VisualEmbedder` e li upserta sul Port `VisualIndex`, iniettando il payload di
sicurezza (RBAC `ruoli_consentiti` + `version_status`) e la provenienza (bbox).

Simmetrico a `extraction/pipeline.run_document`: dipende SOLO dai Port (embedder +
index), quindi l'e2e gira col `FakeVisualIndex` e col Qwen3-VL mascherato, senza
Qdrant ne' GPU. Le pagine testuali pure vengono saltate (vanno al binario testo).

Fail-Closed: un documento senza ruoli e' rifiutato (un `ruoli_consentiti` vuoto
renderebbe il filtro RBAC inutile). ID punto DETERMINISTICO (uuid5 della chiave
naturale doc:page:tile): re-ingest della stessa pagina sovrascrive, niente
duplicati. Design: docs/superpowers/specs/2026-07-09-fase1.1-pixelrag-design.md.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from src.common.roles import Role
from src.retrieval.pixelrag.embedder import VisualEmbedder
from src.retrieval.pixelrag.index import (
    KIND_THUMB,
    KIND_TILE,
    VisualIndex,
    VisualPoint,
)
from src.retrieval.pixelrag.tiling import (
    DEFAULT_DPI,
    DEFAULT_OVERLAP,
    DEFAULT_TILE_PX,
    render_page_tiles,
)

logger = logging.getLogger(__name__)

CURRENT = "current"
# Namespace fisso per gli id deterministici dei punti visivi (uuid5, Qdrant-compat).
_NS = uuid.UUID("6f0d5e2a-1b7c-4e3a-9d21-aa00bb11cc22")


@dataclass(frozen=True)
class IngestOutcome:
    """Riepilogo dell'ingestion visiva di un documento."""

    doc_stem: str
    n_pages: int
    visual_pages: int
    n_points: int
    n_upserted: int


def _default_is_visual(page) -> bool:
    """Pagina da indicizzare visivamente? Riusa il classificatore P2.5 (no modello)."""
    from src.ingestion.page_classifier import classify, page_features

    pc = classify(page_features(page))
    return pc.label == "grafica" or pc.dual


def _point_id(natural: str) -> str:
    """ID deterministico Qdrant-compat (uuid5): stessa chiave naturale -> stesso id."""
    return str(uuid.uuid5(_NS, natural))


def ingest_document(doc, *, doc_stem: str, roles: list[Role], embedder: VisualEmbedder,
                    index: VisualIndex, version_status: str = CURRENT,
                    dpi: int = DEFAULT_DPI, tile_px: int = DEFAULT_TILE_PX,
                    overlap: float = DEFAULT_OVERLAP, include_thumbnail: bool = True,
                    is_visual: Callable[[object], bool] = _default_is_visual) -> IngestOutcome:
    """Ingerisce visivamente le pagine grafiche di UN documento.

    Args:
      doc: documento sfogliabile (es. `fitz.Document`).
      doc_stem: identita' logica del documento.
      roles: ruoli RBAC ammessi al documento (OBBLIGATORIO, fail-closed se vuoto).
      embedder: Port dell'embedder visivo (Qwen3-VL in prod, fake nei test).
      index: Port del motore vettoriale (Qdrant in prod, FakeVisualIndex nei test).
      version_status: stato versione dei punti (default 'current').
      dpi/tile_px/overlap: parametri di tiling.
      include_thumbnail: se True aggiunge un punto thumbnail/pagina (contesto globale).
      is_visual: predicato "questa pagina e' grafica?" (default: classificatore P2.5).

    Ritorna: `IngestOutcome` con i conteggi. Le pagine testuali pure sono saltate.
    """
    if not roles:
        raise ValueError(f"{doc_stem}: nessun ruolo -> RBAC vuoto (fail-closed)")
    ruoli = [str(r) for r in roles]                     # StrEnum -> stringa payload

    points: list[VisualPoint] = []
    n_pages = visual_pages = 0
    for i, page in enumerate(doc, start=1):
        n_pages += 1
        if not is_visual(page):
            continue
        visual_pages += 1

        crops, thumb = render_page_tiles(page, dpi=dpi, tile_px=tile_px, overlap=overlap)
        images = [img for _t, img in crops]
        if include_thumbnail:
            images.append(thumb)                        # un solo batch verso il backend
        vectors = embedder.embed_tiles(images)

        base_payload = {"ruoli_consentiti": ruoli, "version_status": version_status,
                        "doc": doc_stem, "page": i}
        for (tile, _img), vec in zip(crops, vectors):
            points.append(VisualPoint(
                point_id=_point_id(f"{doc_stem}:p{i}:t{tile.row}_{tile.col}"),
                doc=doc_stem, page=i, kind=KIND_TILE, row=tile.row, col=tile.col,
                vector=vec,
                payload={**base_payload, "bbox": [tile.x0, tile.y0, tile.w, tile.h]}))
        if include_thumbnail:
            points.append(VisualPoint(
                point_id=_point_id(f"{doc_stem}:p{i}:thumb"),
                doc=doc_stem, page=i, kind=KIND_THUMB, row=-1, col=-1,
                vector=vectors[-1], payload={**base_payload, "bbox": None}))

    n_upserted = index.upsert(points)
    logger.info("pixelrag.ingest: doc_stem=%s pagine=%d visive=%d punti=%d",
                doc_stem, n_pages, visual_pages, len(points))
    return IngestOutcome(doc_stem=doc_stem, n_pages=n_pages, visual_pages=visual_pages,
                         n_points=len(points), n_upserted=n_upserted)
