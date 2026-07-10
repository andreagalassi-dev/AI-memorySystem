"""Fase 2.1 — Riconciliazione reale end-to-end (Catalogo x Listino) — RED/seam.

Cuce sul motore vero l'intera catena dell'Ipotesi B:
  PgRigheListino (righe_listino reali) -> merge() -> QdrantVisualCatalog (pagine reali)
poi persiste gli scarti con persist_scarti e RILEGGE `scarti_merge` dal DB.

Blinda il PUNTO 6 sui dati veri: il link e' sulla chiave `(doc_stem, page)` che i
due mondi producono 1-indexed. Catalogo misto voluto:
  - riga A: pagina visiva presente e ruolo giusto        -> associazione;
  - riga B: nessuna pagina visiva per il suo (doc,page)   -> scarto (orfano);
  - riga C: pagina visiva presente ma ruolo SBAGLIATO     -> scarto (RBAC fail-closed).

Isolamento critico: schema PG usa-e-getta `test_merge` + collection Qdrant
usa-e-getta, entrambi droppati in teardown. `needs_pg` + `needs_qdrant`.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

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
from src.merge.relational import (
    Associazione,
    MotivoScartoMerge,
    QdrantVisualCatalog,
    merge,
)
from src.merge.store import PgRigheListino, init_schema, persist_scarti
from tests.conftest import needs_pg, needs_qdrant

pytestmark = [needs_pg, needs_qdrant]

SCHEMA = "test_merge"
COLLECTION = "test_merge_recon_ro"
DOC = "mamoli_2021"
DIM = 4


def _riga(codice: str, page: int, prezzo: str) -> RigaListino:
    return RigaListino.from_grezza(
        RigaGrezza(codice_originale=codice, prezzo=prezzo), doc_stem=DOC, page=page)


def _vpage(page: int, ruoli: list[str]) -> models.PointStruct:
    return models.PointStruct(
        id=str(uuid.uuid4()), vector=[0.1] * DIM,
        payload={"doc": DOC, "page": page, "ruoli_consentiti": ruoli,
                 "version_status": "current"})


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
def catalog():
    qc = get_qdrant_client()
    if qc.collection_exists(COLLECTION):
        qc.delete_collection(COLLECTION)
    qc.create_collection(
        COLLECTION,
        vectors_config=models.VectorParams(size=DIM, distance=models.Distance.DOT))
    # A: pagina 3 commerciale (match); C: pagina 5 solo-tecnico (ruolo sbagliato).
    # Nessuna pagina 8 (riga B orfana).
    qc.upsert(COLLECTION, points=[_vpage(3, ["commerciale"]), _vpage(5, ["tecnico"])])
    yield QdrantVisualCatalog(qc, collection=COLLECTION)
    qc.delete_collection(COLLECTION)


def test_riconciliazione_reale_scrive_scarti_su_db(pgconn, catalog):
    upsert_righe(pgconn, [
        _riga("0US-100.A", 3, "12.50"),   # A -> associazione
        _riga("0US-200.B", 8, "5.00"),    # B -> orfano (nessuna pagina visiva)
        _riga("0US-300.C", 5, "7.00"),    # C -> orfano (pagina riservata al tecnico)
    ], schema=SCHEMA)

    rows = PgRigheListino(pgconn, schema=SCHEMA).iter_righe()
    res = merge(rows, catalog, role=Role.COMMERCIALE)

    # 1) esito in memoria: 1 associazione (la A), 2 scarti (B e C)
    assert res.associazioni == [
        Associazione(doc_stem=DOC, page=3, codice_normalizzato="0US100A",
                     codice_originale="0US-100.A")]
    assert {s.page for s in res.scarti} == {8, 5}
    assert all(s.motivo == MotivoScartoMerge.PAGINA_VISIVA_ASSENTE for s in res.scarti)

    # 2) persistenza: gli scarti finiscono in scarti_merge con TUTTI i metadati
    n = persist_scarti(pgconn, res.scarti, schema=SCHEMA)
    assert n == 2

    righe_db = pgconn.execute(sql.SQL(
        "SELECT doc_stem, page, codice_originale, codice_normalizzato_tentato, motivo "
        "FROM {}.scarti_merge ORDER BY page").format(sql.Identifier(SCHEMA))).fetchall()
    assert righe_db == [
        (DOC, 5, "0US-300.C", "0US300C", "pagina_visiva_assente"),
        (DOC, 8, "0US-200.B", "0US200B", "pagina_visiva_assente"),
    ]
