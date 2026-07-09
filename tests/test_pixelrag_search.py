"""Fase 1.1 — Test della ricerca visiva (flusso completo col FakeVisualIndex).

DB-free: embedder fittizio + FakeVisualIndex. Verifica il flusso query->pagine, il
passaggio corretto del filtro RBAC/versione all'indice, il fail-closed sul ruolo e
l'over-fetch dei tile.
"""

from __future__ import annotations

import pytest

from src.common.roles import Role
from src.retrieval.pixelrag.index import KIND_TILE, FakeVisualIndex, VisualPoint
from src.retrieval.pixelrag.search import OVERFETCH, access_filter, visual_search


class _FakeEmbedder:
    """embed_query fisso; embed_tiles non serve alla ricerca."""

    def __init__(self, qvec):
        self.qvec = qvec
        self.seen: list[str] = []

    def embed_query(self, text):
        self.seen.append(text)
        return self.qvec

    def embed_tiles(self, images):
        return []


class _RecordingIndex(FakeVisualIndex):
    """FakeVisualIndex che registra gli argomenti dell'ultima search (per gli assert)."""

    def search(self, query_vector, k, *, flt=None):
        self.last = {"k": k, "flt": flt, "qvec": query_vector}
        return super().search(query_vector, k, flt=flt)


def _pt(pid, doc, page, row, col, vec, **payload):
    payload.setdefault("version_status", "current")
    return VisualPoint(point_id=pid, doc=doc, page=page, kind=KIND_TILE,
                       row=row, col=col, vector=vec, payload=payload)


# ------------------------------------------------------------------- flusso


class TestFlusso:
    def test_query_a_pagine_ordinate(self):
        idx = FakeVisualIndex()
        idx.upsert([
            _pt("p1_a", "D", 1, 0, 0, [0.2, 0.0], ruoli_consentiti=["tecnico"]),
            _pt("p1_b", "D", 1, 0, 1, [1.0, 0.0], ruoli_consentiti=["tecnico"]),  # forte
            _pt("p2_a", "D", 2, 0, 0, [0.6, 0.0], ruoli_consentiti=["tecnico"]),
        ])
        emb = _FakeEmbedder([1.0, 0.0])

        pages = visual_search("esploso pompa", embedder=emb, index=idx, role=Role.TECNICO)

        assert emb.seen == ["esploso pompa"]                 # query embeddata
        assert [(p.doc, p.page) for p in pages] == [("D", 1), ("D", 2)]
        assert pages[0].score == 1.0                         # MaxSim tile della pagina

    def test_cap_a_k_pagine(self):
        idx = FakeVisualIndex()
        idx.upsert([_pt(f"p{n}", "D", n, 0, 0, [1.0, 0.0],
                        ruoli_consentiti=["tecnico"]) for n in range(1, 6)])
        emb = _FakeEmbedder([1.0, 0.0])

        pages = visual_search("x", embedder=emb, index=idx, role=Role.TECNICO, k=2)
        assert len(pages) == 2


# ------------------------------------------------------------------- RBAC


class TestRBAC:
    def test_ruolo_filtra_i_risultati(self):
        idx = FakeVisualIndex()
        idx.upsert([
            _pt("tec", "D", 1, 0, 0, [1.0, 0.0], ruoli_consentiti=["tecnico"]),
            _pt("com", "D", 2, 0, 0, [1.0, 0.0], ruoli_consentiti=["commerciale"]),
        ])
        emb = _FakeEmbedder([1.0, 0.0])

        pages = visual_search("x", embedder=emb, index=idx, role=Role.COMMERCIALE)
        assert [(p.doc, p.page) for p in pages] == [("D", 2)]   # niente pagina tecnica

    def test_filtro_passato_all_indice(self):
        idx = _RecordingIndex()
        idx.upsert([_pt("a", "D", 1, 0, 0, [1.0, 0.0], ruoli_consentiti=["tecnico"])])
        emb = _FakeEmbedder([1.0, 0.0])

        visual_search("x", embedder=emb, index=idx, role=Role.TECNICO, k=3)

        assert idx.last["flt"] == {"ruoli_consentiti": Role.TECNICO,
                                   "version_status": "current"}
        assert idx.last["k"] == 3 * OVERFETCH                 # over-fetch dei tile
        assert idx.last["qvec"] == [1.0, 0.0]

    def test_versione_default_current(self):
        idx = FakeVisualIndex()
        idx.upsert([
            _pt("cur", "D", 1, 0, 0, [1.0, 0.0], ruoli_consentiti=["tecnico"],
                version_status="current"),
            _pt("old", "D", 2, 0, 0, [1.0, 0.0], ruoli_consentiti=["tecnico"],
                version_status="superseded"),
        ])
        emb = _FakeEmbedder([1.0, 0.0])

        pages = visual_search("x", embedder=emb, index=idx, role=Role.TECNICO)
        assert [(p.doc, p.page) for p in pages] == [("D", 1)]   # solo la corrente

    def test_ruolo_obbligatorio_fail_closed(self):
        emb = _FakeEmbedder([1.0, 0.0])
        with pytest.raises(TypeError):
            visual_search("x", embedder=emb, index=FakeVisualIndex(),
                          role="tecnico")  # str libera, non Role


class TestAccessFilter:
    def test_costruzione_filtro(self):
        assert access_filter(Role.TECNICO) == {"ruoli_consentiti": Role.TECNICO,
                                               "version_status": "current"}
        assert access_filter(Role.COMMERCIALE, "superseded")["version_status"] == "superseded"
