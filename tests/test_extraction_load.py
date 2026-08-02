"""Fase 1.2 — Test di persistenza su PostgreSQL (righe valide + scarti).

Integrazione: richiede il Postgres del compose (marker `needs_pg`; skip se giu').
Isolamento: schema usa-e-getta `test_estrazione`, la produzione non viene toccata.
Copre: DDL idempotente, upsert idempotente/aggiornante, scarti con payload jsonb,
e la catena Fail-Closed end-to-end (grounding.partition -> persist).
"""

from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest
from psycopg import sql

from src.backend.clients.postgres_client import conninfo
from src.ingestion.extraction.extractor import FakeExtractor, PageRef
from src.ingestion.extraction.grounding import partition
from src.ingestion.extraction.load import (
    init_schema,
    insert_scarti,
    persist,
    upsert_righe,
)
from src.ingestion.extraction.schema import (
    MotivoScarto,
    RigaGrezza,
    RigaListino,
    Scarto,
)
from tests.conftest import needs_pg

pytestmark = needs_pg

SCHEMA = "test_estrazione"
PAGE_TEXT = "LISTINO\nV0-123 Valvola 12,50\n340-006-734 Guarnizione 3,20\n"


@pytest.fixture()
def pgconn():
    """Connessione su schema estrazione usa-e-getta (drop in setup e teardown)."""
    ident = sql.Identifier(SCHEMA)
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()
        init_schema(c, schema=SCHEMA)
        yield c
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()


def _riga(codice: str, *, prezzo: str | None = None, page: int = 1) -> RigaListino:
    return RigaListino.from_grezza(
        RigaGrezza(codice_originale=codice, prezzo=prezzo), doc_stem="listino_x", page=page)


def _count(conn, table: str) -> int:
    q = sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(SCHEMA), sql.Identifier(table))
    return conn.execute(q).fetchone()[0]


class TestDDL:
    def test_init_schema_idempotente(self, pgconn):
        # gia' creato dalla fixture: rieseguirlo non deve fallire
        init_schema(pgconn, schema=SCHEMA)
        assert _count(pgconn, "righe_listino") == 0
        assert _count(pgconn, "scarti_estrazione") == 0


class TestUpsertRighe:
    def test_inserisce(self, pgconn):
        n = upsert_righe(pgconn, [_riga("V0-123", prezzo="12.50")], schema=SCHEMA)
        assert n == 1 and _count(pgconn, "righe_listino") == 1
        row = pgconn.execute(sql.SQL(
            "SELECT codice_normalizzato, prezzo FROM {}.righe_listino"
        ).format(sql.Identifier(SCHEMA))).fetchone()
        assert row[0] == "V0123" and row[1] == Decimal("12.50")

    def test_upsert_idempotente_no_duplicati(self, pgconn):
        r = _riga("V0-123", prezzo="12.50")
        upsert_righe(pgconn, [r], schema=SCHEMA)
        upsert_righe(pgconn, [r], schema=SCHEMA)  # stessa chiave
        assert _count(pgconn, "righe_listino") == 1

    def test_upsert_aggiunge_nuovo_prezzo_se_diverso(self, pgconn):
        upsert_righe(pgconn, [_riga("V0-123", prezzo="12.50")], schema=SCHEMA)
        upsert_righe(pgconn, [_riga("V0-123", prezzo="9.90")], schema=SCHEMA)
        prezzi = pgconn.execute(sql.SQL(
            "SELECT prezzo FROM {}.righe_listino ORDER BY prezzo"
        ).format(sql.Identifier(SCHEMA))).fetchall()
        assert len(prezzi) == 2
        assert prezzi[0][0] == Decimal("9.90")
        assert prezzi[1][0] == Decimal("12.50")

    def test_lista_vuota_no_op(self, pgconn):
        assert upsert_righe(pgconn, [], schema=SCHEMA) == 0


class TestInsertScarti:
    def test_registra_payload_jsonb_e_motivo(self, pgconn):
        s = Scarto(doc_stem="listino_x", page=1, codice_originale="V0-777",
                   codice_normalizzato_tentato="V0777",
                   payload_raw={"prezzo": "88.00"},
                   motivo=MotivoScarto.CODICE_NON_ANCORATO)
        assert insert_scarti(pgconn, [s], schema=SCHEMA) == 1
        row = pgconn.execute(sql.SQL(
            "SELECT motivo, payload_raw FROM {}.scarti_estrazione"
        ).format(sql.Identifier(SCHEMA))).fetchone()
        assert row[0] == "codice_non_ancorato"
        assert row[1] == {"prezzo": "88.00"}  # jsonb -> dict


class TestFailClosedEndToEnd:
    def test_valide_su_righe_allucinato_su_scarti(self, pgconn):
        # il modello (fake) propone un codice reale e uno fabbricato
        fake = FakeExtractor({("listino_x", 1): [
            RigaGrezza(codice_originale="V0-123", prezzo="12.50"),
            RigaGrezza(codice_originale="V0-777", prezzo="88.00"),  # non nella pagina
        ]})
        righe = [RigaListino.from_grezza(g, doc_stem="listino_x", page=1)
                 for g in fake.extract(PageRef("listino_x", 1))]
        valide, scarti = partition(righe, PAGE_TEXT)

        summary = persist(pgconn, valide, scarti, schema=SCHEMA)

        assert summary == {"righe_upsert": 1, "scarti": 1}
        assert _count(pgconn, "righe_listino") == 1
        assert _count(pgconn, "scarti_estrazione") == 1
        cod = pgconn.execute(sql.SQL(
            "SELECT codice_normalizzato FROM {}.righe_listino"
        ).format(sql.Identifier(SCHEMA))).fetchone()[0]
        assert cod == "V0123"  # solo il codice ancorato ha attraversato la barriera
