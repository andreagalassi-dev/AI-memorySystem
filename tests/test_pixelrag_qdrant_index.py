"""Fase 1.3 — Adapter reale `QdrantVisualIndex` (Port `VisualIndex`) su Qdrant.

Due livelli, entrambi sul motore vero (`needs_qdrant`):
 1) collection multivettore USA-E-GETTA: upsert + search + traduzione del filtro RBAC
    in `models.Filter` — prova che ruolo/versione filtrano davvero (fail-closed, no leak);
 2) prova RBAC ECONOMICA su dato REALE (`aike_pages_visual`, 1035 pagine) con un
    multivettore DUMMY: nessun modello caricato, ma si verifica che come `tecnico`
    tornino hit reali e come `commerciale` zero (le pagine sono ruolo-tecnico).

Il grounding loop col modello Qwen3-VL vero e' separato (manuale), non un unit test.
"""

from __future__ import annotations

import uuid

import pytest
from qdrant_client import models

from src.backend.clients.qdrant_client import get_qdrant_client
from src.common.roles import Role
from src.retrieval.pixelrag.index import QdrantVisualIndex, VisualHit, VisualPoint
from src.retrieval.pixelrag.search import access_filter, visual_search
from tests.conftest import needs_qdrant

TEST_COLLECTION = "test_pixelrag_ro"
DIM = 4


def _mv(*rows: list[float]) -> list[list[float]]:
    """Un multivettore (lista di vettori-token)."""
    return [list(r) for r in rows]


def _point(pid: str, doc: str, page: int, ruoli: list[str], vec: list[list[float]],
           vstatus: str = "current") -> VisualPoint:
    return VisualPoint(
        point_id=pid, doc=doc, page=page, kind="page", row=-1, col=-1, vector=vec,
        payload={"ruoli_consentiti": ruoli, "version_status": vstatus,
                 "doc": doc, "page": page})


@pytest.fixture()
def disposable_index():
    qc = get_qdrant_client()
    if qc.collection_exists(TEST_COLLECTION):
        qc.delete_collection(TEST_COLLECTION)
    qc.create_collection(
        TEST_COLLECTION,
        vectors_config=models.VectorParams(
            size=DIM, distance=models.Distance.DOT,
            multivector_config=models.MultiVectorConfig(
                comparator=models.MultiVectorComparator.MAX_SIM),
            hnsw_config=models.HnswConfigDiff(m=0)))
    idx = QdrantVisualIndex(qc, collection=TEST_COLLECTION)
    idx.upsert([
        _point("11111111-1111-1111-1111-111111111111", "solo_tecnico", 5,
               ["tecnico"], _mv([1, 0, 0, 0])),
        _point("22222222-2222-2222-2222-222222222222", "condiviso", 9,
               ["tecnico", "commerciale"], _mv([1, 0, 0, 0])),
        _point("33333333-3333-3333-3333-333333333333", "vecchio", 2,
               ["tecnico"], _mv([1, 0, 0, 0]), vstatus="superseded"),
    ])
    yield idx
    qc.delete_collection(TEST_COLLECTION)


@needs_qdrant
class TestQdrantVisualIndexDisposable:
    def test_search_mappa_su_visualhit(self, disposable_index):
        hits = disposable_index.search(_mv([1, 0, 0, 0]), 10,
                                       flt=access_filter(Role.TECNICO))
        assert hits and all(isinstance(h, VisualHit) for h in hits)
        docs = {h.doc for h in hits}
        assert "solo_tecnico" in docs and "condiviso" in docs  # tecnico vede current
        assert "vecchio" not in docs                            # 'superseded' escluso

    def test_rbac_commerciale_no_leak(self, disposable_index):
        hits = disposable_index.search(_mv([1, 0, 0, 0]), 10,
                                       flt=access_filter(Role.COMMERCIALE))
        docs = {h.doc for h in hits}
        assert docs == {"condiviso"}   # NON vede la pagina solo-tecnico

    def test_visual_search_end_to_end_con_dummy_embedder(self, disposable_index):
        # pixelrag completo (embed->search->pages_from_tiles) su Qdrant vero, no modello
        class _DummyEmb:
            def embed_query(self, text): return _mv([1, 0, 0, 0])
            def embed_tiles(self, images): return []
        pages = visual_search("qualsiasi", embedder=_DummyEmb(),
                              index=disposable_index, role=Role.COMMERCIALE, k=3)
        assert [p.doc for p in pages] == ["condiviso"]


@needs_qdrant
class TestQdrantVisualIndexDatoReale:
    """Prova RBAC economica sui 1035 punti reali (nessun modello caricato).

    Dato reale: 1035 pagine, TUTTE ruolo-tecnico, di cui solo 43 condivise col
    commerciale. Query DUMMY leggera (1 token) con k grande: il commerciale puo'
    ricevere al massimo le sue 43 pagine (il filtro RBAC e' applicato davvero), il
    tecnico molte di piu' -> asimmetria fail-closed sull'adapter, su dato reale.
    Una query di un'intera pagina (~800 token) sarebbe infattibile col MaxSim
    brute-force; una query reale e' invece piccola come questa."""

    def test_tecnico_trova_pagine_reali(self):
        idx = QdrantVisualIndex(get_qdrant_client())  # default aike_pages_visual
        hits = idx.search([0.1] * 1024, 5, flt=access_filter(Role.TECNICO))
        assert hits, "il tecnico deve vedere pagine reali"

    def test_rbac_asimmetrico_filtra_su_dato_reale(self):
        idx = QdrantVisualIndex(get_qdrant_client())
        q = [0.1] * 1024
        com = {(h.doc, h.page) for h in
               idx.search(q, 10000, flt=access_filter(Role.COMMERCIALE))}
        tec = {(h.doc, h.page) for h in
               idx.search(q, 10000, flt=access_filter(Role.TECNICO))}
        # il filtro RBAC e' applicato: il commerciale e' limitato alle sue 43 pagine
        assert 0 < len(com) <= 43
        # ...e vede STRETTAMENTE meno del tecnico (no leak delle pagine solo-tecnico)
        assert len(tec) > len(com)
        assert com < tec   # subset proprio: nulla di esclusivo al commerciale


@needs_qdrant
class TestDefaultDepsVisualWiring:
    def test_visual_search_cablato(self):
        from src.common.roles import Role
        from src.graph.pipeline import _default_deps
        deps = _default_deps(Role.TECNICO)
        assert callable(deps.visual_search)   # ramo VISUAL cablato su Qdrant reale
