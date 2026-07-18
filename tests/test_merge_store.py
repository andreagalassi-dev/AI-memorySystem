"""Fase 2.1 — Persistenza degli scarti di merge su PostgreSQL (SSOT) — RED.

Integrazione: richiede il Postgres del compose (marker `needs_pg`; skip se giu').
Isolamento: schema usa-e-getta `test_merge`, la produzione non viene toccata.
Copre: DDL idempotente della tabella `scarti_merge` (DISTINTA da `scarti_estrazione`
di 1.2) e la persistenza di uno `ScartoMerge` con motivo + provenienza per la
dashboard umana (§2.2: codice originale, codice normalizzato tentato, pagina + PDF
sorgente come link di verifica). Sicurezza: identificatori via `psycopg.sql`, mai
f-string sul nome schema.
"""

from __future__ import annotations

import psycopg
import pytest
from psycopg import sql

from src.backend.clients.postgres_client import conninfo
from src.merge.relational import MotivoScartoMerge, ScartoMerge
from src.merge.store import init_schema, persist_scarti
from tests.conftest import needs_pg

pytestmark = needs_pg

SCHEMA = "test_merge"


@pytest.fixture()
def pgconn():
    """Connessione su schema merge usa-e-getta (drop in setup e teardown)."""
    ident = sql.Identifier(SCHEMA)
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()
        init_schema(c, schema=SCHEMA)
        yield c
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()


def _count(conn, table: str) -> int:
    q = sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(SCHEMA), sql.Identifier(table))
    return conn.execute(q).fetchone()[0]


def _scarto(doc_stem: str = "mamoli_2021", page: int = 3) -> ScartoMerge:
    return ScartoMerge(
        doc_stem=doc_stem,
        page=page,
        codice_originale="0US-123.A",
        codice_normalizzato_tentato="0US123A",
        motivo=MotivoScartoMerge.PAGINA_VISIVA_ASSENTE,
    )


class TestDDL:
    def test_init_schema_idempotente(self, pgconn):
        # gia' creato dalla fixture: rieseguirlo non deve fallire
        init_schema(pgconn, schema=SCHEMA)
        assert _count(pgconn, "scarti_merge") == 0


class TestPersistScarti:
    def test_registra_motivo_e_provenienza(self, pgconn):
        assert persist_scarti(pgconn, [_scarto()], schema=SCHEMA) == 1
        row = pgconn.execute(sql.SQL(
            "SELECT doc_stem, page, codice_originale, codice_normalizzato_tentato, motivo "
            "FROM {}.scarti_merge"
        ).format(sql.Identifier(SCHEMA))).fetchone()
        assert row == ("mamoli_2021", 3, "0US-123.A", "0US123A", "pagina_visiva_assente")

    def test_created_at_valorizzato(self, pgconn):
        persist_scarti(pgconn, [_scarto()], schema=SCHEMA)
        created = pgconn.execute(sql.SQL(
            "SELECT created_at FROM {}.scarti_merge"
        ).format(sql.Identifier(SCHEMA))).fetchone()[0]
        assert created is not None  # timestamp di default (audit dashboard)

    def test_lista_vuota_no_op(self, pgconn):
        assert persist_scarti(pgconn, [], schema=SCHEMA) == 0
        assert _count(pgconn, "scarti_merge") == 0
