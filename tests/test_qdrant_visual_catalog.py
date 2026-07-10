"""Fase 2.1 — Adapter reale `QdrantVisualCatalog` (Port `VisualCatalog`) — RED.

Il catalogo visivo reale e' Qdrant `aike_pages_visual` (un punto = una PAGINA).
`pages_for(doc_stem, *, role)` NON e' una vector-search: e' uno **scroll** per
payload (`doc == doc_stem` + RBAC `ruoli_consentiti` contiene il ruolo, versione
`current`) che raccoglie i numeri di pagina distinti. Serve alla cucitura
deterministica riga-prezzo <-> pagina.

Tre requisiti architetturali (test su motore vero, `needs_qdrant`, collection
usa-e-getta ricreata per test):
 1) Isolamento: collection disposable.
 2) Paginazione: piu' punti del `scroll_batch` -> il Port DEVE ciclare su
    `next_page_offset`, senza troncare il set (un troncamento = falsi orfani).
 3) RBAC fail-closed: un ruolo X non estrae pagine il cui payload ammette solo Y/Z.
"""

from __future__ import annotations

import uuid

import pytest
from qdrant_client import models

from src.backend.clients.qdrant_client import get_qdrant_client
from src.common.roles import Role
from src.merge.relational import QdrantVisualCatalog
from tests.conftest import needs_qdrant

TEST_COLLECTION = "test_visual_catalog_ro"
DIM = 4


def _point(doc: str, page: int, ruoli: list[str], vstatus: str = "current") -> models.PointStruct:
    """Un punto-pagina (vettore dummy: lo scroll filtra per payload, non per vettore)."""
    return models.PointStruct(
        id=str(uuid.uuid4()),
        vector=[0.1] * DIM,
        payload={"doc": doc, "page": page, "ruoli_consentiti": ruoli,
                 "version_status": vstatus},
    )


@pytest.fixture()
def qc_collection():
    """Client + collection disposable RICREATA per test (isolamento)."""
    qc = get_qdrant_client()
    if qc.collection_exists(TEST_COLLECTION):
        qc.delete_collection(TEST_COLLECTION)
    qc.create_collection(
        TEST_COLLECTION,
        vectors_config=models.VectorParams(size=DIM, distance=models.Distance.DOT))
    yield qc
    qc.delete_collection(TEST_COLLECTION)


@needs_qdrant
class TestQdrantVisualCatalog:
    def test_pages_for_ritorna_le_pagine_del_documento(self, qc_collection):
        qc_collection.upsert(TEST_COLLECTION, points=[
            _point("mamoli_2021", 3, ["commerciale"]),
            _point("mamoli_2021", 7, ["commerciale"]),
        ])
        cat = QdrantVisualCatalog(qc_collection, collection=TEST_COLLECTION)

        assert cat.pages_for("mamoli_2021", role=Role.COMMERCIALE) == {3, 7}

    def test_scroll_pagina_oltre_il_limit_non_tronca(self, qc_collection):
        # 5 pagine con scroll_batch=2 -> il Port deve ciclare (2+2+1), non fermarsi a 2.
        qc_collection.upsert(TEST_COLLECTION, points=[
            _point("mamoli_2021", p, ["commerciale"]) for p in (1, 2, 3, 4, 5)])
        cat = QdrantVisualCatalog(qc_collection, collection=TEST_COLLECTION, scroll_batch=2)

        assert cat.pages_for("mamoli_2021", role=Role.COMMERCIALE) == {1, 2, 3, 4, 5}

    def test_rbac_fail_closed_ruolo_diverso_non_estrae_pagine(self, qc_collection):
        qc_collection.upsert(TEST_COLLECTION, points=[
            _point("mamoli_2021", 3, ["tecnico", "amministrazione"]),  # non commerciale
        ])
        cat = QdrantVisualCatalog(qc_collection, collection=TEST_COLLECTION)

        assert cat.pages_for("mamoli_2021", role=Role.COMMERCIALE) == set()

    def test_pagine_di_altro_documento_non_perdono(self, qc_collection):
        qc_collection.upsert(TEST_COLLECTION, points=[
            _point("mamoli_2021", 3, ["commerciale"]),
            _point("altro_doc", 3, ["commerciale"]),   # stesso page, doc diverso
        ])
        cat = QdrantVisualCatalog(qc_collection, collection=TEST_COLLECTION)

        assert cat.pages_for("mamoli_2021", role=Role.COMMERCIALE) == {3}
