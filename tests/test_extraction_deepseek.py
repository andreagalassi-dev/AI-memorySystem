"""Fase 1.2 — Test dell'adapter DeepSeek (client Instructor MOCKATO, render REALE).

Il modello e' mockato (nessuna rete), ma il rendering della pagina e' quello vero di
PyMuPDF: osserviamo il PNG realmente prodotto ed embeddato come data-URI. Copre il
contratto TableExtractor, la costruzione del messaggio multimodale e il fail-closed
sul rendering.
"""

from __future__ import annotations

import base64

import fitz
import pytest

from src.ingestion.extraction.deepseek import DeepSeekExtractor, PageRenderError
from src.ingestion.extraction.extractor import PageRef, TableExtractor
from src.ingestion.extraction.schema import RigaGrezza


class _FakeCompletions:
    def __init__(self, outcome):
        self._outcome = outcome
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, outcome):
        self.chat = _FakeChat(_FakeCompletions(outcome))


def _doc_una_pagina() -> fitz.Document:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "V0-123 Valvola 12,50", fontsize=10)
    return doc


def _user_content(client) -> list:
    return client.chat.completions.last_kwargs["messages"][1]["content"]


class TestContratto:
    def test_e_un_table_extractor(self):
        adapter = DeepSeekExtractor(_doc_una_pagina(), _FakeClient([]), model="deepseek-ocr")
        assert isinstance(adapter, TableExtractor)

    def test_extract_ritorna_le_righe_del_modello(self):
        rows = [RigaGrezza(codice_originale="V0-123", prezzo="12.50")]
        client = _FakeClient(rows)
        adapter = DeepSeekExtractor(_doc_una_pagina(), client, model="deepseek-ocr",
                                    max_retries=4)

        out = adapter.extract(PageRef("listino_x", 1))

        assert out is rows
        kw = client.chat.completions.last_kwargs
        assert kw["response_model"] == list[RigaGrezza]
        assert kw["model"] == "deepseek-ocr" and kw["max_retries"] == 4


class TestRenderMultimodale:
    def test_messaggio_contiene_immagine_png_reale(self):
        client = _FakeClient([])
        DeepSeekExtractor(_doc_una_pagina(), client, model="m").extract(PageRef("d", 1))

        content = _user_content(client)
        kinds = [part["type"] for part in content]
        assert "text" in kinds and "image_url" in kinds

        img = next(p for p in content if p["type"] == "image_url")
        uri = img["image_url"]["url"]
        assert uri.startswith("data:image/png;base64,")
        # osserva l'output reale del renderer: il PNG decodifica ed e' non vuoto
        raw = base64.b64decode(uri.split(",", 1)[1])
        assert raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) > 100

    def test_dpi_piu_alto_produce_immagine_piu_grande(self):
        doc = _doc_una_pagina()
        c_lo, c_hi = _FakeClient([]), _FakeClient([])
        DeepSeekExtractor(doc, c_lo, model="m", dpi=72).extract(PageRef("d", 1))
        DeepSeekExtractor(doc, c_hi, model="m", dpi=200).extract(PageRef("d", 1))

        lo = base64.b64decode(_user_content(c_lo)[1]["image_url"]["url"].split(",", 1)[1])
        hi = base64.b64decode(_user_content(c_hi)[1]["image_url"]["url"].split(",", 1)[1])
        assert len(hi) > len(lo)  # 200dpi rende piu' pixel del 72dpi


class TestFailClosed:
    def test_pagina_inesistente_solleva(self):
        adapter = DeepSeekExtractor(_doc_una_pagina(), _FakeClient([]), model="m")
        with pytest.raises(PageRenderError):
            adapter.extract(PageRef("d", 99))  # documento ha 1 sola pagina

    def test_errore_del_modello_propagato(self):
        boom = RuntimeError("retry esauriti")
        adapter = DeepSeekExtractor(_doc_una_pagina(), _FakeClient(boom), model="m")
        with pytest.raises(RuntimeError, match="retry esauriti"):
            adapter.extract(PageRef("d", 1))
