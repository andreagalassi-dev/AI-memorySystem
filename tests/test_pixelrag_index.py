"""Fase 1.1 — Test del Port VisualIndex, del FakeVisualIndex e della ricomposizione.

Tutto DB-free: upsert/search in memoria (dot-product), filtro RBAC/versioning e
ricomposizione tile->pagina via MaxSim. Nessun Qdrant, nessuna GPU.
"""

from __future__ import annotations

from src.retrieval.pixelrag.index import (
    KIND_TILE,
    FakeVisualIndex,
    PageHit,
    VisualHit,
    VisualIndex,
    VisualPoint,
    pages_from_tiles,
)


def _pt(pid, doc, page, row, col, vec, **payload):
    return VisualPoint(point_id=pid, doc=doc, page=page, kind=KIND_TILE,
                       row=row, col=col, vector=vec, payload=payload)


# ------------------------------------------------------- ricomposizione MaxSim


class TestPagesFromTiles:
    def test_score_pagina_e_max_dei_tile(self):
        hits = [
            VisualHit("a", "D", 3, KIND_TILE, 0, 0, 0.20),
            VisualHit("b", "D", 3, KIND_TILE, 0, 1, 0.90),   # tile piu' forte
            VisualHit("c", "D", 3, KIND_TILE, 1, 0, 0.50),
        ]
        pages = pages_from_tiles(hits)
        assert pages == [PageHit(doc="D", page=3, score=0.90, n_tiles=3)]

    def test_pagine_distinte_ordinate_per_score(self):
        hits = [
            VisualHit("a", "D", 1, KIND_TILE, 0, 0, 0.30),
            VisualHit("b", "D", 2, KIND_TILE, 0, 0, 0.80),
            VisualHit("c", "E", 1, KIND_TILE, 0, 0, 0.55),
        ]
        pages = pages_from_tiles(hits)
        assert [(p.doc, p.page, p.score) for p in pages] == [
            ("D", 2, 0.80), ("E", 1, 0.55), ("D", 1, 0.30)]

    def test_doc_e_page_sono_la_chiave(self):
        # stesso page number ma doc diverso -> pagine distinte
        hits = [VisualHit("a", "D", 1, KIND_TILE, 0, 0, 0.4),
                VisualHit("b", "E", 1, KIND_TILE, 0, 0, 0.7)]
        pages = pages_from_tiles(hits)
        assert {(p.doc, p.page) for p in pages} == {("D", 1), ("E", 1)}

    def test_vuoto(self):
        assert pages_from_tiles([]) == []


# ------------------------------------------------------- FakeVisualIndex


class TestFakeVisualIndex:
    def test_e_un_visual_index(self):
        assert isinstance(FakeVisualIndex(), VisualIndex)

    def test_upsert_e_ricerca_topk_per_similarita(self):
        idx = FakeVisualIndex()
        idx.upsert([
            _pt("p1", "D", 1, 0, 0, [1.0, 0.0]),
            _pt("p2", "D", 1, 0, 1, [0.0, 1.0]),
            _pt("p3", "D", 2, 0, 0, [0.9, 0.1]),
        ])
        hits = idx.search([1.0, 0.0], k=2)
        assert [h.point_id for h in hits] == ["p1", "p3"]   # piu' vicini a [1,0]
        assert hits[0].score > hits[1].score

    def test_reupsert_idempotente(self):
        idx = FakeVisualIndex()
        idx.upsert([_pt("p1", "D", 1, 0, 0, [1.0, 0.0])])
        idx.upsert([_pt("p1", "D", 1, 0, 0, [0.5, 0.5])])   # stesso id
        assert len(idx.search([1.0, 1.0], k=10)) == 1

    def test_filtro_rbac_ruolo(self):
        idx = FakeVisualIndex()
        idx.upsert([
            _pt("t", "D", 1, 0, 0, [1.0, 0.0], ruoli_consentiti=["tecnico"]),
            _pt("c", "D", 2, 0, 0, [1.0, 0.0], ruoli_consentiti=["commerciale"]),
        ])
        hits = idx.search([1.0, 0.0], k=10, flt={"ruoli_consentiti": "tecnico"})
        assert [h.point_id for h in hits] == ["t"]   # il commerciale non affiora

    def test_filtro_versione_scalare(self):
        idx = FakeVisualIndex()
        idx.upsert([
            _pt("cur", "D", 1, 0, 0, [1.0, 0.0], version_status="current"),
            _pt("old", "D", 2, 0, 0, [1.0, 0.0], version_status="superseded"),
        ])
        hits = idx.search([1.0, 0.0], k=10, flt={"version_status": "current"})
        assert [h.point_id for h in hits] == ["cur"]

    def test_filtro_combinato_and(self):
        idx = FakeVisualIndex()
        idx.upsert([_pt("ok", "D", 1, 0, 0, [1.0, 0.0],
                        ruoli_consentiti=["tecnico"], version_status="current")])
        assert idx.search([1.0, 0.0], k=10,
                          flt={"ruoli_consentiti": "commerciale",
                               "version_status": "current"}) == []


# ------------------------------------------------ integrazione: search -> pagine


class TestSearchPoiRicomposizione:
    def test_flusso_completo_tile_alla_pagina(self):
        idx = FakeVisualIndex()
        idx.upsert([
            _pt("p1_t00", "D", 1, 0, 0, [0.2, 0.0]),
            _pt("p1_t01", "D", 1, 0, 1, [1.0, 0.0]),   # tile forte della pagina 1
            _pt("p2_t00", "D", 2, 0, 0, [0.6, 0.0]),
        ])
        pages = pages_from_tiles(idx.search([1.0, 0.0], k=10))
        assert pages[0].doc == "D" and pages[0].page == 1     # vince la pagina 1
        assert pages[0].score == 1.0 and pages[0].n_tiles == 2
