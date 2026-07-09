"""Fase 1.2 — Test end-to-end dell'orchestratore (FakeExtractor -> Postgres).

E2E veloce e reale: PDF sintetico via fitz (una pagina-tabella + una di prosa),
FakeExtractor al posto di "modello + enforce", fino alle tabelle Postgres. Dimostra
la firma del prodotto: il dato reale arriva in righe_listino, l'allucinato negli
scarti, la prosa viene saltata. Richiede il Postgres del compose (marker needs_pg).
"""

from __future__ import annotations

import fitz
import psycopg
import pytest
from psycopg import sql

from src.backend.clients.postgres_client import conninfo
from src.ingestion.extraction.extractor import FakeExtractor
from src.ingestion.extraction.load import init_schema
from src.ingestion.extraction.pipeline import run_document
from src.ingestion.extraction.schema import RigaGrezza
from tests.conftest import needs_pg

pytestmark = needs_pg

SCHEMA = "test_estrazione_pipe"
DOC = "listino_x"


@pytest.fixture()
def pgconn():
    ident = sql.Identifier(SCHEMA)
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()
        init_schema(c, schema=SCHEMA)
        yield c
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()


def _doc_una_tabella_una_prosa() -> fitz.Document:
    """Pagina 1: tabella rigata con i codici a testo. Pagina 2: sola prosa."""
    doc = fitz.open()
    p1 = doc.new_page()
    for y in (100, 120, 140, 160):
        p1.draw_line((72, y), (320, y))
    for x in (72, 160, 240, 320):
        p1.draw_line((x, 100), (x, 160))
    p1.insert_text((80, 112), "V0-123 Valvola 12,50", fontsize=8)
    p1.insert_text((80, 132), "340-006-734 Guarnizione 3,20", fontsize=8)

    p2 = doc.new_page()
    p2.insert_text((72, 72), "\n".join(
        "Avvertenze di sicurezza generali: leggere il manuale." for _ in range(8)),
        fontsize=9)
    return doc


def _count(conn, table: str) -> int:
    q = sql.SQL("SELECT count(*) FROM {}.{}").format(sql.Identifier(SCHEMA), sql.Identifier(table))
    return conn.execute(q).fetchone()[0]


def test_e2e_fake_extractor_fino_al_db(pgconn):
    # il "modello" (fake) propone un codice reale e uno fabbricato sulla pagina 1
    fake = FakeExtractor({(DOC, 1): [
        RigaGrezza(codice_originale="V0-123", prezzo="12.50"),   # ancorato nel testo
        RigaGrezza(codice_originale="V0-999", prezzo="99.00"),   # allucinato
    ]})
    doc = _doc_una_tabella_una_prosa()

    out = run_document(pgconn, doc, doc_stem=DOC, extractor=fake, schema=SCHEMA)
    doc.close()

    # esito aggregato
    assert out.n_pages == 2
    assert out.table_pages == 1          # solo la pagina 1 e' tabella
    assert out.righe_upsert == 1         # solo V0-123 supera il grounding
    assert out.scarti == 1               # V0-999 fabbricato -> scarto

    # esito per-pagina
    assert out.pages[0].is_table and out.pages[1].is_table is False

    # stato reale del database
    assert _count(pgconn, "righe_listino") == 1
    assert _count(pgconn, "scarti_estrazione") == 1
    cod = pgconn.execute(sql.SQL(
        "SELECT codice_normalizzato FROM {}.righe_listino").format(sql.Identifier(SCHEMA))
    ).fetchone()[0]
    assert cod == "V0123"


def test_e2e_documento_senza_tabelle_non_scrive(pgconn):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "\n".join(
        "Introduzione al manuale e note legali." for _ in range(6)), fontsize=9)

    out = run_document(pgconn, doc, doc_stem="solo_prosa", extractor=FakeExtractor(),
                       schema=SCHEMA)
    doc.close()

    assert out.table_pages == 0 and out.righe_upsert == 0 and out.scarti == 0
    assert _count(pgconn, "righe_listino") == 0


def test_e2e_reingest_idempotente(pgconn):
    fake = FakeExtractor({(DOC, 1): [RigaGrezza(codice_originale="V0-123", prezzo="12.50")]})
    doc = _doc_una_tabella_una_prosa()

    run_document(pgconn, doc, doc_stem=DOC, extractor=fake, schema=SCHEMA)
    run_document(pgconn, doc, doc_stem=DOC, extractor=fake, schema=SCHEMA)  # re-ingest
    doc.close()

    # upsert idempotente: la riga non si duplica
    assert _count(pgconn, "righe_listino") == 1
