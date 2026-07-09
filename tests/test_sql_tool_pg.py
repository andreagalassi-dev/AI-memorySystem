"""Fase 1.3 — Gate del ruolo DB read-only per il ramo STRUCTURED (difesa in profondita').

`sql_tool` e' gia' anti-injection a livello applicativo (whitelist + `%s`, provato in
`test_graph_sql_tool.py::TestPgSqlExecutor`). Qui verifichiamo l'ULTIMA linea: anche
se qualcosa aggirasse la whitelist, la CONNESSIONE con cui gira il ramo STRUCTURED e'
un ruolo Postgres **SELECT-only**, quindi il DB stesso rifiuta ogni scrittura.

Il test e' auto-contenuto: crea il ruolo read-only in modo idempotente sullo schema
usa-e-getta e apre una seconda connessione con quel ruolo. Prova empirica, non
asserzione: un SELECT via `PgSqlExecutor` funziona; un INSERT/UPDATE con lo stesso
ruolo solleva `InsufficientPrivilege`. Skip se il Postgres del compose e' giu'.
"""

from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest
from psycopg import errors, sql

from src.backend.clients.postgres_client import conninfo
from src.backend.config import settings
from src.graph.tools.sql_tool import LookupRow, PgSqlExecutor, lookup
from src.ingestion.extraction.load import init_schema, upsert_righe
from src.ingestion.extraction.schema import RigaGrezza, RigaListino
from tests.conftest import needs_pg

PG_SCHEMA = "test_sql_tool_ro"
RO_USER = "aike_ro_test"          # ruolo di test, ricreato idempotente
RO_PWD = "aike_ro_test_pwd"       # dev-only, mai in produzione
CODE = "V0-123"


def _riga(codice: str, *, prezzo: str, page: int = 1) -> RigaListino:
    return RigaListino.from_grezza(
        RigaGrezza(codice_originale=codice, prezzo=prezzo), doc_stem="listino_x", page=page)


def _ro_conninfo() -> str:
    """Come `conninfo()` ma con le credenziali del ruolo read-only."""
    return (f"host={settings.postgres_host} port={settings.postgres_port} "
            f"user={RO_USER} password={RO_PWD} dbname={settings.postgres_db}")


@pytest.fixture()
def ro_env():
    """Schema usa-e-getta + ruolo SELECT-only; yield (conn_admin, conn_ro, schema)."""
    ident = sql.Identifier(PG_SCHEMA)
    with psycopg.connect(conninfo()) as adm:
        adm.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        adm.commit()
        init_schema(adm, schema=PG_SCHEMA)
        upsert_righe(adm, [_riga(CODE, prezzo="12.50")], schema=PG_SCHEMA)

        # ruolo read-only idempotente + GRANT SELECT solo sullo schema usa-e-getta
        adm.execute(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{RO_USER}') "
            f"THEN CREATE ROLE {RO_USER} LOGIN PASSWORD '{RO_PWD}'; END IF; END $$;")
        adm.execute(f"GRANT USAGE ON SCHEMA {PG_SCHEMA} TO {RO_USER}")
        adm.execute(f"GRANT SELECT ON {PG_SCHEMA}.righe_listino TO {RO_USER}")
        adm.commit()

        ro = psycopg.connect(_ro_conninfo())
        try:
            yield adm, ro, PG_SCHEMA
        finally:
            ro.close()
            # DROP OWNED revoca i GRANT verso il ruolo -> DROP ROLE puo' riuscire
            adm.rollback()
            adm.execute(f"DROP OWNED BY {RO_USER}")
            adm.execute(f"DROP ROLE IF EXISTS {RO_USER}")
            adm.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
            adm.commit()


@needs_pg
class TestRuoloReadOnly:
    def test_select_funziona_col_ruolo_readonly(self, ro_env):
        _adm, ro, schema = ro_env
        rows = lookup(PgSqlExecutor(ro, schema=schema), CODE, "quanto costa")
        assert rows == [LookupRow(valore=Decimal("12.50"), doc_stem="listino_x", page=1)]

    def test_insert_rifiutato_dal_db(self, ro_env):
        _adm, ro, schema = ro_env
        with pytest.raises(errors.InsufficientPrivilege):
            with ro.cursor() as cur:
                cur.execute(sql.SQL(
                    "INSERT INTO {}.righe_listino "
                    "(doc_stem, page, codice_normalizzato, codice_originale) "
                    "VALUES ('x', 9, 'HACK', 'HACK')").format(sql.Identifier(schema)))
        ro.rollback()

    def test_update_rifiutato_dal_db(self, ro_env):
        _adm, ro, schema = ro_env
        with pytest.raises(errors.InsufficientPrivilege):
            with ro.cursor() as cur:
                cur.execute(sql.SQL(
                    "UPDATE {}.righe_listino SET prezzo = 0").format(sql.Identifier(schema)))
        ro.rollback()

    def test_delete_rifiutato_dal_db(self, ro_env):
        _adm, ro, schema = ro_env
        with pytest.raises(errors.InsufficientPrivilege):
            with ro.cursor() as cur:
                cur.execute(sql.SQL(
                    "DELETE FROM {}.righe_listino").format(sql.Identifier(schema)))
        ro.rollback()
