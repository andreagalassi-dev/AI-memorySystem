from __future__ import annotations

import os
from unittest import mock

import fitz
import psycopg
import pytest
from psycopg import sql

from src.backend.clients.postgres_client import conninfo
from src.ingestion.extraction.load import init_schema
from src.ingestion.extraction.extractor import FakeExtractor
from src.ingestion.run_massive_extraction import run_document_massive
from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer
from tests.conftest import needs_pg

pytestmark = needs_pg

SCHEMA = "test_docling_wiring"

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


def _make_table_pdf(path: str) -> None:
    doc = fitz.open()
    page = doc.new_page()
    # Add enough text to pass table_detect (n_words > 10)
    page.insert_textbox(
        fitz.Rect(50, 20, 500, 50),
        "Questo testo serve per superare la guardia table_detect che richiede n_words minimo per non scartare la pagina.",
        fontsize=10
    )
    
    rows = [
        ("AB-1234", "Valvola A", "12.50"),
    ]
    y = 72
    for cod, desc, prezzo in rows:
        page.insert_text((72, y), cod, fontsize=9)
        page.insert_text((180, y), desc, fontsize=9)
        page.insert_text((360, y), prezzo, fontsize=9)
    doc.save(path)
    doc.close()


def test_docling_wiring_opt_in(pgconn, tmp_path, monkeypatch):
    from src.ingestion.run_massive_extraction import get_text_extractor_factory
    
    pdf = tmp_path / "tab.pdf"
    
    # Extract page 8 from a real born-digital PDF
    source_doc = fitz.open("corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf")
    new_doc = fitz.open()
    new_doc.insert_pdf(source_doc, from_page=7, to_page=7)
    new_doc.save(str(pdf))
    new_doc.close()
    source_doc.close()

    monkeypatch.setenv("AIKE_TABLE_EXTRACTOR", "docling")

    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)
    extractor = FakeExtractor({})

    result = run_document_massive(
        str(pdf),
        conn=pgconn,
        extractor=extractor,
        multiplexer=mux,
        model_name="glm-ocr",
        schema=SCHEMA,
        text_extractor_factory=get_text_extractor_factory(),
    )

    assert result.righe_upsert >= 1
    
    # Verify DB
    db_rows = pgconn.execute(
        sql.SQL("SELECT codice_originale, prezzo FROM {}.righe_listino").format(sql.Identifier(SCHEMA))
    ).fetchall()
    
    assert len(db_rows) >= 1
    # Check that Docling logic was applied (e.g. short codes or alphanumeric found correctly)
    codes = {row[0] for row in db_rows}
    assert "8524" in codes or "10023" in codes or "14557" in codes or "90416C" in codes or "10011" in codes or "3082" in codes



def test_docling_wiring_default(pgconn, tmp_path, monkeypatch):
    from src.ingestion.run_massive_extraction import get_text_extractor_factory
    
    monkeypatch.delenv("AIKE_TABLE_EXTRACTOR", raising=False)
    
    factory = get_text_extractor_factory()
    from src.ingestion.extraction.text_table import TextTableExtractor
    assert factory is TextTableExtractor
