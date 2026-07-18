"""Fase 1.1 — Test end-to-end dell'ingestion visiva (render REALE, embed/index fake).

Rendering PyMuPDF vero (osserviamo tile reali); embedder Qwen3-VL MASCHERATO (funzioni
iniettate, no torch) e FakeVisualIndex. Verifica: selezione pagine grafiche, tile +
thumbnail, payload RBAC, id deterministici (re-ingest idempotente) e il roundtrip
ingest -> visual_search filtrato per ruolo.
"""

from __future__ import annotations

import fitz
import pytest

from src.common.roles import Role
from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder
from src.retrieval.pixelrag.index import KIND_THUMB, KIND_TILE, FakeVisualIndex
from src.retrieval.pixelrag.ingest import ingest_document
from src.retrieval.pixelrag.search import visual_search


def _fake_embedder():
    # un embedding [1,0] per ogni immagine (tile o thumbnail); nessun torch
    return Qwen3VLEmbedder(embed_pages=lambda imgs: [[1.0, 0.0]] * len(imgs),
                           embed_query=lambda t: [1.0, 0.0])


def _doc_grafica_e_prosa() -> fitz.Document:
    """Pagina 1: raster pieno (grafica). Pagina 2: prosa (testuale, da saltare)."""
    doc = fitz.open()
    p1 = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 240, 240), False)
    pix.clear_with(128)
    p1.insert_image(p1.rect, stream=pix.tobytes("png"))

    p2 = doc.new_page()
    p2.insert_text((72, 72), "\n".join(
        "Avvertenze di sicurezza: leggere il manuale prima dell uso." for _ in range(10)),
        fontsize=9)
    return doc


class TestIngestE2E:
    def test_indicizza_solo_le_pagine_grafiche(self):
        idx = FakeVisualIndex()
        out = ingest_document(_doc_grafica_e_prosa(), doc_stem="esploso_x",
                              roles=[Role.TECNICO], embedder=_fake_embedder(), index=idx)

        assert out.n_pages == 2 and out.visual_pages == 1   # la prosa e' saltata
        assert out.n_points == out.n_upserted and out.n_points >= 2  # tile(i) + thumb

    def test_tile_e_thumbnail_presenti_con_payload_rbac(self):
        idx = FakeVisualIndex()
        ingest_document(_doc_grafica_e_prosa(), doc_stem="D", roles=[Role.TECNICO],
                        embedder=_fake_embedder(), index=idx)

        pts = list(idx._points.values())
        kinds = {p.kind for p in pts}
        assert KIND_TILE in kinds and KIND_THUMB in kinds
        assert sum(p.kind == KIND_THUMB for p in pts) == 1     # un solo thumbnail
        for p in pts:
            assert p.payload["ruoli_consentiti"] == ["tecnico"]
            assert p.payload["version_status"] == "current"
            assert p.doc == "D" and p.page == 1
        # i tile portano il bbox, il thumbnail no
        tile = next(p for p in pts if p.kind == KIND_TILE)
        assert len(tile.payload["bbox"]) == 4

    def test_reingest_idempotente_id_deterministici(self):
        idx = FakeVisualIndex()
        emb = _fake_embedder()
        doc = _doc_grafica_e_prosa()
        n1 = ingest_document(doc, doc_stem="D", roles=[Role.TECNICO],
                             embedder=emb, index=idx).n_points
        # re-ingest dello stesso documento: stessi id -> nessun duplicato
        ingest_document(_doc_grafica_e_prosa(), doc_stem="D", roles=[Role.TECNICO],
                        embedder=emb, index=idx)
        assert len(idx._points) == n1

    def test_roundtrip_ingest_poi_ricerca_filtrata_per_ruolo(self):
        idx = FakeVisualIndex()
        emb = _fake_embedder()
        ingest_document(_doc_grafica_e_prosa(), doc_stem="esploso_x",
                        roles=[Role.TECNICO], embedder=emb, index=idx)

        # il tecnico trova la pagina grafica...
        pages = visual_search("esploso", embedder=emb, index=idx, role=Role.TECNICO)
        assert (pages[0].doc, pages[0].page) == ("esploso_x", 1)
        # ...il commerciale no (RBAC nel payload)
        assert visual_search("esploso", embedder=emb, index=idx,
                             role=Role.COMMERCIALE) == []

    def test_documento_senza_ruoli_rifiutato(self):
        with pytest.raises(ValueError):
            ingest_document(_doc_grafica_e_prosa(), doc_stem="D", roles=[],
                            embedder=_fake_embedder(), index=FakeVisualIndex())

    def test_thumbnail_disattivabile(self):
        idx = FakeVisualIndex()
        ingest_document(_doc_grafica_e_prosa(), doc_stem="D", roles=[Role.TECNICO],
                        embedder=_fake_embedder(), index=idx, include_thumbnail=False)
        assert all(p.kind == KIND_TILE for p in idx._points.values())
