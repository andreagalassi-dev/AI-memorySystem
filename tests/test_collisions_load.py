import logging
import pytest
import psycopg
from psycopg import sql

from src.ingestion.extraction.schema import RigaListino
from src.ingestion.extraction.load import init_schema, upsert_righe, _DDL_RIGHE, _DDL_SCARTI
from src.ingestion.extraction.pipeline import run_document
from src.retrieval.price_variants import aggregate_price_variants, PriceVariant, PriceAnswer
from src.graph.tools.sql_tool import LookupRow

from decimal import Decimal

# Helper per connessione db di test
@pytest.fixture
def conn():
    with psycopg.connect("host=localhost port=5432 user=aike password=aike_dev dbname=aike_registry") as c:
        yield c

@pytest.fixture
def scratch_schema(conn):
    schema_name = "estrazione_test_collision"
    conn.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(sql.Identifier(schema_name)))
    conn.commit()
    yield schema_name
    conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema_name)))
    conn.commit()

def test_migration_and_upsert_preserves_multiple_prices(conn, scratch_schema, caplog):
    caplog.set_level(logging.WARNING)
    # (a) MIGRAZIONE: creiamo tabella col VECCHIO DDL
    old_ddl = sql.SQL("""
    CREATE TABLE IF NOT EXISTS {schema}.righe_listino (
        doc_stem            text        NOT NULL,
        page                int         NOT NULL,
        codice_normalizzato text        NOT NULL,
        codice_originale    text        NOT NULL,
        descrizione         text,
        prezzo              numeric,
        updated_at          timestamptz NOT NULL DEFAULT now(),
        PRIMARY KEY (doc_stem, page, codice_normalizzato)
    )
    """).format(schema=sql.Identifier(scratch_schema))
    
    conn.execute(old_ddl)
    conn.execute(_DDL_SCARTI.format(schema=sql.Identifier(scratch_schema)))
    conn.commit()

    # Inseriamo 1 riga
    riga1 = RigaListino(
        doc_stem="doc1", page=1, codice_normalizzato="COD1", codice_originale="cod1",
        descrizione="Desc", prezzo=Decimal("10.00")
    )
    # Usiamo vecchio upsert (per simulare DB pre-migrazione)
    q = sql.SQL("""
    INSERT INTO {schema}.righe_listino (doc_stem, page, codice_normalizzato, codice_originale, descrizione, prezzo, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, now())
    """).format(schema=sql.Identifier(scratch_schema))
    conn.execute(q, (riga1.doc_stem, riga1.page, riga1.codice_normalizzato, riga1.codice_originale, riga1.descrizione, riga1.prezzo))
    conn.commit()

    # Eseguiamo il NUOVO init_schema
    init_schema(conn, schema=scratch_schema)

    # (a) Verifica che si possa inserire una seconda riga con stesso doc, page, codice, ma prezzo diverso (2 righe)
    riga2 = RigaListino(
        doc_stem="doc1", page=1, codice_normalizzato="COD1", codice_originale="cod1",
        descrizione="Desc 2", prezzo=Decimal("20.00")
    )
    
    # (b) UPSERT preserva
    upsert_righe(conn, [riga2], schema=scratch_schema)
    
    cur = conn.execute(sql.SQL("SELECT prezzo FROM {schema}.righe_listino ORDER BY prezzo").format(schema=sql.Identifier(scratch_schema)))
    prezzi = [r[0] for r in cur.fetchall()]
    assert len(prezzi) == 2
    assert prezzi == [Decimal("10.00"), Decimal("20.00")]

    # (c) IDEMPOTENZA: ri-upsert stessa (code, prezzo)
    upsert_righe(conn, [riga2], schema=scratch_schema)
    cur = conn.execute(sql.SQL("SELECT prezzo FROM {schema}.righe_listino ORDER BY prezzo").format(schema=sql.Identifier(scratch_schema)))
    assert len(cur.fetchall()) == 2

    # (d) NULL
    riga_null_1 = RigaListino(doc_stem="doc1", page=1, codice_normalizzato="COD_NULL", codice_originale="codnull", descrizione="", prezzo=None)
    riga_null_2 = RigaListino(doc_stem="doc1", page=1, codice_normalizzato="COD_NULL", codice_originale="codnull", descrizione="", prezzo=None)
    upsert_righe(conn, [riga_null_1, riga_null_2], schema=scratch_schema)
    
    cur = conn.execute(sql.SQL("SELECT count(*) FROM {schema}.righe_listino WHERE codice_normalizzato='COD_NULL'").format(schema=sql.Identifier(scratch_schema)))
    assert cur.fetchone()[0] == 1


class FakePage:
    def get_text(self, opt): return "mock text"

class FakeVerdict:
    is_table = True
    reason = "mock"

class FakeExtractor:
    def extract(self, pageref):
        from src.ingestion.extraction.schema import RigaGrezza
        # Testiamo una pagina con due prezzi e una pagina normale
        if pageref.page == 1:
            return [
                RigaGrezza(codice_originale="COD1", descrizione="Desc", prezzo="10.00"),
                RigaGrezza(codice_originale="COD1", descrizione="Desc", prezzo="20.00")
            ]
        return []

def test_pipeline_guard_logger(conn, scratch_schema, caplog):
    caplog.set_level(logging.WARNING)
    extractor = FakeExtractor()
    doc = [FakePage()]
    
    from unittest.mock import patch
    with patch("src.ingestion.extraction.pipeline.is_table_page", return_value=FakeVerdict()):
        run_document(conn, doc, doc_stem="docguard", extractor=extractor, schema=scratch_schema)
    
    # (e) GUARD emette logger.warning
    assert any("COD1" in record.message and "docguard" in record.message for record in caplog.records if record.levelno == logging.WARNING)


def test_aggregator_same_page():
    # (f) AGGREGATOR
    rows = [
        LookupRow(doc_stem="doc1", page=1, 
                  valore=Decimal("10.00"), 
                  ingested_at="2026-08-01", version_status=None),
        LookupRow(doc_stem="doc1", page=1, 
                  valore=Decimal("20.00"), 
                  ingested_at="2026-08-01", version_status=None),
    ]
    ans = aggregate_price_variants(rows)
    assert ans.principale is not None
    assert len(ans.varianti) == 1
    assert 1 in ans.principale.pagine
    assert 1 in ans.varianti[0].pagine


def test_persist_tx_production_path_preserves_and_warns(conn, scratch_schema, caplog):
    # Il path di PRODUZIONE (ingest_full -> run_document_massive -> _persist_tx) deve
    # preservare i due prezzi E loggare la collisione (mai muta), come il path pipeline.
    from src.ingestion.run_massive_extraction import _persist_tx
    caplog.set_level(logging.WARNING)
    init_schema(conn, schema=scratch_schema)
    r1 = RigaListino(doc_stem="docmass", page=5, codice_normalizzato="M1",
                     codice_originale="m1", descrizione="d", prezzo=Decimal("10.00"))
    r2 = RigaListino(doc_stem="docmass", page=5, codice_normalizzato="M1",
                     codice_originale="m1", descrizione="d2", prezzo=Decimal("20.00"))
    _persist_tx(conn, [r1, r2], [], schema=scratch_schema)
    conn.commit()
    cur = conn.execute(sql.SQL(
        "SELECT count(*) FROM {schema}.righe_listino WHERE codice_normalizzato='M1'"
    ).format(schema=sql.Identifier(scratch_schema)))
    assert cur.fetchone()[0] == 2
    assert any("M1" in r.message and "docmass" in r.message
               for r in caplog.records if r.levelno == logging.WARNING)
