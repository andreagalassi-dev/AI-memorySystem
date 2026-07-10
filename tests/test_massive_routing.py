"""Fase 1.2 — Routing per-pagina del binario HEAVY: testo esatto vs GLM-OCR.

Regola architetturale: una pagina-tabella con text-layer nativo affidabile
(born-digital) va all'estrattore testuale (codici esatti); solo le VERE scansioni
(senza testo) restano a GLM-OCR (che sulle born-digital corromperebbe i codici).

Test DB-free: esercitano solo la funzione di scelta, senza Postgres ne' modelli.
"""

from __future__ import annotations

from src.ingestion.extraction.extractor import FakeExtractor
from src.ingestion.run_massive_extraction import route_table_page


class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def get_text(self, kind: str) -> str:
        return self._text


def test_born_digital_va_al_text_extractor():
    text_ex, vlm_ex = FakeExtractor(), FakeExtractor()
    ex, label = route_table_page(
        _FakePage("x" * 200), text_extractor=text_ex, vlm_extractor=vlm_ex
    )
    assert ex is text_ex
    assert label == "HEAVY_TABLE_TEXT"


def test_scansione_muta_va_a_glm_ocr():
    text_ex, vlm_ex = FakeExtractor(), FakeExtractor()
    ex, label = route_table_page(
        _FakePage("   "), text_extractor=text_ex, vlm_extractor=vlm_ex
    )
    assert ex is vlm_ex
    assert label == "HEAVY_TABLE_VLM"


def test_senza_text_extractor_resta_su_glm_ocr():
    """Retrocompat: se il chiamante non inietta il text-extractor, si usa il VLM."""
    vlm_ex = FakeExtractor()
    ex, label = route_table_page(
        _FakePage("x" * 200), text_extractor=None, vlm_extractor=vlm_ex
    )
    assert ex is vlm_ex
    assert label == "HEAVY_TABLE_VLM"
