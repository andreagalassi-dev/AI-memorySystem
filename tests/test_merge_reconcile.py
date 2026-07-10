"""Fase 2.1 — Orchestratore di riconciliazione a corpus (`reconcile_corpus`) — RED.

Itera l'intero `righe_listino` (un `doc_stem` alla volta), esegue `merge()` contro
il catalogo visivo e persiste gli scarti in `scarti_merge`. Vincoli:

- **Idempotenza**: un re-run NON duplica gli scarti; per ogni doc si fa delete
  mirato (`WHERE doc_stem`) + insert, atomici -> anche gli scarti diventati obsoleti
  (doc ora senza orfani) vengono purgati.
- **Commit per doc_stem**: un crash a meta' corpus lascia coerenti i doc gia' fatti.
- Anti-OOM: mai l'intero corpus in RAM (un documento alla volta).

Isolamento (`needs_pg`+`needs_qdrant`): schema PG + collection Qdrant usa-e-getta.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from psycopg import sql
from qdrant_client import models

from src.backend.clients.postgres_client import conninfo
from src.backend.clients.qdrant_client import get_qdrant_client
from src.common.roles import Role
from src.ingestion.extraction.load import init_schema as init_extraction_schema
from src.ingestion.extraction.load import upsert_righe
from src.ingestion.extraction.schema import RigaGrezza, RigaListino
from src.merge.reconcile import reconcile_corpus
from src.merge.relational import QdrantVisualCatalog
from src.merge.store import init_schema
from tests.conftest import needs_pg, needs_qdrant

pytestmark = [needs_pg, needs_qdrant]

SCHEMA = "test_merge"
COLLECTION = "test_merge_reconcile_ro"
DOC = "mamoli_2021"
DIM = 4


def _riga(codice: str, page: int, prezzo: str = "10.00") -> RigaListino:
    return RigaListino.from_grezza(
        RigaGrezza(codice_originale=codice, prezzo=prezzo), doc_stem=DOC, page=page)


def _vpage(qc, page: int, ruoli: list[str]) -> None:
    qc.upsert(COLLECTION, points=[models.PointStruct(
        id=str(uuid.uuid4()), vector=[0.1] * DIM,
        payload={"doc": DOC, "page": page, "ruoli_consentiti": ruoli,
                 "version_status": "current"})])


def _count_scarti(conn) -> int:
    return conn.execute(sql.SQL("SELECT count(*) FROM {}.scarti_merge").format(
        sql.Identifier(SCHEMA))).fetchone()[0]


@pytest.fixture()
def pgconn():
    ident = sql.Identifier(SCHEMA)
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()
        init_extraction_schema(c, schema=SCHEMA)   # righe_listino
        init_schema(c, schema=SCHEMA)               # scarti_merge
        yield c
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()


@pytest.fixture()
def qc():
    client = get_qdrant_client()
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(
        COLLECTION,
        vectors_config=models.VectorParams(size=DIM, distance=models.Distance.DOT))
    yield client
    client.delete_collection(COLLECTION)


def _seed_mixed(pgconn, qc):
    """A=pagina 3 commerciale (match); B=pagina 8 assente (orfano); C=pagina 5 tecnico
    (ruolo sbagliato -> orfano per il commerciale)."""
    upsert_righe(pgconn, [_riga("0US-100.A", 3), _riga("0US-200.B", 8),
                          _riga("0US-300.C", 5)], schema=SCHEMA)
    _vpage(qc, 3, ["commerciale"])
    _vpage(qc, 5, ["tecnico"])


def test_rerun_non_duplica_scarti(pgconn, qc):
    _seed_mixed(pgconn, qc)
    catalog = QdrantVisualCatalog(qc, collection=COLLECTION)

    rep1 = reconcile_corpus(pgconn, catalog, role=Role.COMMERCIALE, schema=SCHEMA)
    assert (rep1.docs, rep1.associazioni, rep1.scarti) == (1, 1, 2)
    assert _count_scarti(pgconn) == 2

    # secondo giro identico: idempotente, nessun duplicato
    reconcile_corpus(pgconn, catalog, role=Role.COMMERCIALE, schema=SCHEMA)
    assert _count_scarti(pgconn) == 2


def test_rerun_purga_scarti_obsoleti(pgconn, qc):
    _seed_mixed(pgconn, qc)
    catalog = QdrantVisualCatalog(qc, collection=COLLECTION)

    reconcile_corpus(pgconn, catalog, role=Role.COMMERCIALE, schema=SCHEMA)
    assert _count_scarti(pgconn) == 2

    # ora le pagine 8 e 5 diventano visibili al commerciale: B e C non sono piu' orfani
    _vpage(qc, 8, ["commerciale"])
    _vpage(qc, 5, ["commerciale"])

    rep = reconcile_corpus(pgconn, catalog, role=Role.COMMERCIALE, schema=SCHEMA)
    assert rep.scarti == 0
    assert _count_scarti(pgconn) == 0   # gli scarti obsoleti sono stati purgati


def test_dry_run_non_scrive_ma_conta(pgconn, qc):
    _seed_mixed(pgconn, qc)
    catalog = QdrantVisualCatalog(qc, collection=COLLECTION)

    rep = reconcile_corpus(pgconn, catalog, role=Role.COMMERCIALE, schema=SCHEMA,
                           dry_run=True)

    # il report riflette la realta' (1 associazione, 2 orfani)...
    assert (rep.docs, rep.associazioni, rep.scarti) == (1, 1, 2)
    # ...ma il dry-run e' read-only: niente scritto in scarti_merge
    assert _count_scarti(pgconn) == 0
