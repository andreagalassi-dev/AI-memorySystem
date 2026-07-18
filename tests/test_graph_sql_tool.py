"""Fase 1.3 — Test del tool SQL sicuro (Text-to-Parameters + query template).

Due strati di verifica:
  - DB-free (default): estrazione parametri deterministica, whitelist chiusa delle
    colonne, costruzione query con `psycopg.sql`, `FakeSqlExecutor` in memoria.
  - `needs_pg`: query reale su uno schema usa-e-getta (pattern `test_extraction_load`).

Il fuoco e' ANTI-INJECTION: nessun input dell'utente/LLM deve poter alterare la
struttura della query. Il codice viaggia solo come parametro `%s`; la colonna solo
da whitelist chiusa. Un `DROP TABLE` in ingresso deve essere neutralizzato, non
eseguito. Fail-closed: codice assente -> nessuna riga (poi "non lo so" a valle).
"""

from __future__ import annotations

from decimal import Decimal

import psycopg
import pytest
from psycopg import sql

from src.graph.tools.sql_tool import (
    ALLOWED_COLUMNS,
    FakeSqlExecutor,
    LookupRow,
    PgSqlExecutor,
    SqlExecutor,
    SqlParams,
    _build_query,
    extract_params,
    lookup,
)


class TestExtractParams:
    """Text-to-Parameters: nessun SQL, solo codice normalizzato + campo da whitelist."""

    def test_codice_normalizzato_via_canonical(self):
        p = extract_params("V0-123", "quanto costa")
        assert p.codice_normalizzato == "V0123"

    def test_default_campo_e_prezzo(self):
        # intent STRUCTURED tipico = prezzo; anche query di solo codice cade qui
        assert extract_params("V0-123", "V0-123").campo == "prezzo"

    def test_keyword_prezzo(self):
        for q in ("quanto costa", "prezzo del ricambio", "what is the price", "was kostet"):
            assert extract_params("V0-123", q).campo == "prezzo"

    def test_keyword_descrizione(self):
        for q in ("dammi la descrizione", "che cos'e questo", "give me the description"):
            assert extract_params("V0-123", q).campo == "descrizione"

    def test_codice_vuoto_dopo_normalizzazione_fail_closed(self):
        # "---" non ha alfanumerici -> nessuna query, mai un codice fantasma
        with pytest.raises(ValueError, match="normalizzazione"):
            extract_params("---", "prezzo")

    def test_injection_nel_codice_ridotta_ad_alfanumerico(self):
        # canonical elimina ogni metacarattere SQL: sopravvivono solo [0-9A-Z]
        p = extract_params("0; DROP TABLE righe_listino; --", "prezzo")
        assert p.codice_normalizzato == "0DROPTABLERIGHELISTINO"
        assert p.campo == "prezzo"


class TestBuildQuery:
    """La colonna proviene SOLO dalla whitelist chiusa, composta con sql.Identifier."""

    def test_query_usa_identifier_quotato_e_placeholder(self):
        q = _build_query("prezzo", "estrazione")
        rendered = q.as_string(None)
        assert '"prezzo"' in rendered            # identifier quotato, non interpolato
        assert '"estrazione".righe_listino' in rendered
        assert "%s" in rendered                  # codice come parametro, mai in stringa
        assert "codice_normalizzato = %s" in rendered

    def test_solo_righe_listino_come_tabella(self):
        assert "righe_listino" in _build_query("descrizione", "estrazione").as_string(None)

    def test_campo_fuori_whitelist_rifiutato(self):
        for campo in ("secret", "*", "prezzo; DROP TABLE righe_listino", "prezzo, updated_at",
                      "doc_stem"):
            with pytest.raises(ValueError, match="whitelist"):
                _build_query(campo, "estrazione")

    def test_whitelist_chiusa_e_ristretta(self):
        assert ALLOWED_COLUMNS == frozenset({"prezzo", "descrizione", "codice_originale"})


class TestFakeSqlExecutor:
    def test_e_un_sql_executor(self):
        assert isinstance(FakeSqlExecutor(), SqlExecutor)

    def test_codice_presente_ritorna_riga_con_provenienza(self):
        ex = FakeSqlExecutor({"V0123": {"prezzo": Decimal("12.50"),
                                        "doc_stem": "listino_x", "page": 3}})
        rows = ex.run(SqlParams(codice_normalizzato="V0123", campo="prezzo"))
        assert rows == [LookupRow(valore=Decimal("12.50"), doc_stem="listino_x", page=3)]

    def test_codice_assente_ritorna_vuoto_fail_closed(self):
        ex = FakeSqlExecutor({"V0123": {"prezzo": Decimal("12.50"),
                                        "doc_stem": "x", "page": 1}})
        assert ex.run(SqlParams(codice_normalizzato="ASSENTE", campo="prezzo")) == []

    def test_campo_fuori_whitelist_rifiutato_anche_nel_fake(self):
        # difesa in profondita': anche il Fake rifiuta una colonna non whitelisted
        ex = FakeSqlExecutor()
        with pytest.raises(ValueError, match="whitelist"):
            ex.run(SqlParams(codice_normalizzato="V0123", campo="updated_at"))


class TestLookup:
    """Il tie deterministico usato dal nodo del grafo: params -> executor -> righe."""

    def test_lookup_end_to_end_con_fake(self):
        ex = FakeSqlExecutor({"V0123": {"prezzo": Decimal("12.50"),
                                        "doc_stem": "listino_x", "page": 3}})
        rows = lookup(ex, "V0-123", "quanto costa")
        assert rows == [LookupRow(valore=Decimal("12.50"), doc_stem="listino_x", page=3)]

    def test_injection_neutralizzata_nessuna_riga(self):
        # il payload malevolo diventa un codice inesistente: 0 righe, nessun effetto
        ex = FakeSqlExecutor({"V0123": {"prezzo": Decimal("1"), "doc_stem": "x", "page": 1}})
        assert lookup(ex, "0; DROP TABLE righe_listino; --", "prezzo") == []


# ---------------------------------------------------------------------------
# Integrazione: query reale su Postgres (schema usa-e-getta). Skip se DB giu'.
# ---------------------------------------------------------------------------

from tests.conftest import needs_pg  # noqa: E402

from src.backend.clients.postgres_client import conninfo  # noqa: E402
from src.ingestion.extraction.load import init_schema, upsert_righe  # noqa: E402
from src.ingestion.extraction.schema import RigaGrezza, RigaListino  # noqa: E402

PG_SCHEMA = "test_sql_tool"


def _riga(codice: str, *, prezzo: str, page: int = 1) -> RigaListino:
    return RigaListino.from_grezza(
        RigaGrezza(codice_originale=codice, prezzo=prezzo), doc_stem="listino_x", page=page)


@pytest.fixture()
def pgconn():
    ident = sql.Identifier(PG_SCHEMA)
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()
        init_schema(c, schema=PG_SCHEMA)
        upsert_righe(c, [_riga("V0-123", prezzo="12.50")], schema=PG_SCHEMA)
        yield c
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()


@needs_pg
class TestPgSqlExecutor:
    def test_lookup_reale_ritorna_prezzo_e_provenienza(self, pgconn):
        ex = PgSqlExecutor(conninfo(), schema=PG_SCHEMA)
        rows = lookup(ex, "V0-123", "quanto costa")
        assert rows == [LookupRow(valore=Decimal("12.50"), doc_stem="listino_x", page=1)]

    def test_codice_assente_fail_closed(self, pgconn):
        ex = PgSqlExecutor(conninfo(), schema=PG_SCHEMA)
        assert lookup(ex, "ZZ-999", "quanto costa") == []

    def test_injection_non_esegue_drop_e_ritorna_vuoto(self, pgconn):
        ex = PgSqlExecutor(conninfo(), schema=PG_SCHEMA)
        rows = lookup(ex, "0; DROP TABLE righe_listino; --", "prezzo")
        assert rows == []
        # prova regina: la tabella esiste ancora con la sua riga, il DROP non e' passato
        n = pgconn.execute(sql.SQL(
            "SELECT count(*) FROM {}.righe_listino").format(sql.Identifier(PG_SCHEMA))).fetchone()[0]
        assert n == 1

    def test_campo_fuori_whitelist_non_raggiunge_il_db(self, pgconn):
        ex = PgSqlExecutor(conninfo(), schema=PG_SCHEMA)
        with pytest.raises(ValueError, match="whitelist"):
            ex.run(SqlParams(codice_normalizzato="V0123", campo="updated_at"))
