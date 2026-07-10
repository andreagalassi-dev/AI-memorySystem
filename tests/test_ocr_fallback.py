"""Fase 1 — Fallback OCR: invarianti architetturali — RED.

Valida che:
1. needs_ocr rileva correttamente le pagine senza text-layer (scan puro).
2. FakeOcr ritorna testo programmato (per i test del runner).
3. VisionOcr e' importabile senza ocrmac installato (import lazy).
4. L'integrazione nel runner: una pagina HEAVY senza text-layer usa l'OCR fallback
   come base per il grounding, invece di scartare tutto.
"""

from __future__ import annotations

import logging

import fitz
import psycopg
import pytest
from psycopg import sql

from src.backend.clients.postgres_client import conninfo
from src.ingestion.extraction.extractor import FakeExtractor
from src.ingestion.extraction.load import init_schema
from src.ingestion.extraction.schema import RigaGrezza
from src.ingestion.ocr_fallback import FakeOcr, VisionOcr, needs_ocr
from src.ingestion.run_massive_extraction import run_document_massive
from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer
from tests.conftest import needs_pg

pytestmark = needs_pg

SCHEMA = "test_ocr_fallback"


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


def _make_scan_pdf(path: str) -> None:
    """Crea un PDF 'scan puro': pagina con immagine ma nessun text-layer."""
    doc = fitz.open()
    page = doc.new_page()
    # inserisce un'immagine bitmap (rettangolo colorato) senza testo
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 200, 200))
    pix.set_rect(fitz.IRect(0, 0, 200, 200), (200, 200, 200))
    page.insert_image(fitz.Rect(36, 36, 300, 300), pixmap=pix)
    doc.save(path)
    doc.close()


def test_needs_ocr_rileva_scan_puro(tmp_path):
    """needs_ocr ritorna True per pagine con text-layer insufficiente."""
    pdf = tmp_path / "scan.pdf"
    _make_scan_pdf(str(pdf))

    doc = fitz.open(str(pdf))
    page = doc[0]
    assert needs_ocr(page) is True, "pagina scan puro dovrebbe richiedere OCR"
    doc.close()


def test_needs_ocr_rifiuta_pagina_con_testo():
    """needs_ocr ritorna False per pagine con text-layer ricco."""
    doc = fitz.open()
    page = doc.new_page()
    long_text = "codice AB-1234 descrizione valvola prezzo 12.50 " * 20
    page.insert_text((72, 72), long_text, fontsize=9)
    assert needs_ocr(page) is False, "pagina con testo non dovrebbe richiedere OCR"
    doc.close()


def test_fake_ocr_ritorna_testo_programmato():
    """FakeOcr ritorna il testo con cui e' costruito."""
    fake = FakeOcr(text="CODICE001 DESC 12.50")
    doc = fitz.open()
    page = doc.new_page()
    assert fake.extract_text(page) == "CODICE001 DESC 12.50"
    doc.close()


def test_vision_ocr_importabile_senza_ocrmac():
    """VisionOcr si istanzia anche senza ocrmac installato (import lazy)."""
    ocr = VisionOcr()
    assert ocr is not None


@needs_pg
def test_runner_usa_ocr_fallback_per_scan_puro(pgconn, tmp_path, caplog):
    """Integrazione: una pagina HEAVY senza text-layer usa l'OCR per il grounding.

    Costruisamo un PDF con una pagina-tabella che ha solo immagini (scan puro).
    Il runner rileva needs_ocr=True, invoca l'OCR fallback (FakeOcr con codici
    reali), e il grounding valida le righe estratte contro il testo OCR.
    """
    # PDF con una pagina che ha immagini ma NESSUN text-layer
    pdf = tmp_path / "scan_table.pdf"
    doc = fitz.open()
    page = doc.new_page()
    # inserisce una griglia di linee (simula tabella) + immagini (nessun testo)
    for y in range(72, 300, 14):
        page.draw_line(fitz.Point(72, y), fitz.Point(400, y), color=(0, 0, 0))
    for x in range(72, 400, 80):
        page.draw_line(fitz.Point(x, 72), fitz.Point(x, 300), color=(0, 0, 0))
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50))
    pix.set_rect(fitz.IRect(0, 0, 50, 50), (255, 255, 255))
    page.insert_image(fitz.Rect(80, 80, 130, 100), pixmap=pix)
    doc.save(str(pdf))
    doc.close()

    # Il runner usa FakeOcr che ritorna un testo con codici "reali"
    ocr_text = "AB-1234 Valvola 12.50 CD-5678 Filtro 8.30"
    ocr = FakeOcr(text=ocr_text)

    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)
    # FakeExtractor emette codici che sono presenti nel testo OCR
    extractor = FakeExtractor(
        {
            ("scan_table", 1): [
                RigaGrezza(codice_originale="AB-1234", descrizione="Valvola", prezzo="12.50"),
                RigaGrezza(codice_originale="CD-5678", descrizione="Filtro", prezzo="8.30"),
                RigaGrezza(codice_originale="FABBRICATO", descrizione="Falso", prezzo="99.99"),
            ],
        }
    )

    caplog.set_level(logging.INFO)
    result = run_document_massive(
        str(pdf),
        conn=pgconn,
        extractor=extractor,
        multiplexer=mux,
        model_name="glm-ocr",
        schema=SCHEMA,
        ocr=ocr,
    )

    # I codici AB-1234 e CD-5678 sono nel testo OCR -> validati.
    # FABBRICATO non e' nel testo OCR -> scartato.
    assert result.righe_upsert == 2, f"attese 2 righe valide, got {result.righe_upsert}"
    assert result.scarti == 1, f"atteso 1 scarto, got {result.scarti}"
    # il log deve menzionare l'OCR fallback
    assert "ocr" in caplog.text.lower() or "OCR" in caplog.text
