"""Fase 1.0 — Test del Fast-Dispatch.

Unit puri su `route_for` (mappa classe->rotta) + integrazione con PDF sintetici
generati via fitz (ermetici, deterministici): niente corpus, niente rete.
Copre i modi di fallimento rumorosi: PDF cifrato, file corrotto, file mancante.
"""

from __future__ import annotations

import asyncio

import fitz
import pytest

from src.ingestion.fast_dispatch import (
    CorruptPdfError,
    DocumentDispatch,
    EncryptedPdfError,
    PageDispatch,
    Route,
    dispatch_pdf,
    dispatch_pdf_async,
    route_for,
)
from src.ingestion.page_classifier import PageClass

TEXT_MARKER = "Codice AB-12345-X descrizione ricambio quantita prezzo listino."


# ------------------------------------------------------------------- helper PDF


def _add_text_page(doc: fitz.Document) -> None:
    """Pagina born-digital solo-testo: ~15 righe, chars ben oltre T_CHARS=300."""
    page = doc.new_page()
    body = "\n".join(f"{TEXT_MARKER} riga {n:02d}" for n in range(15))
    page.insert_text((72, 72), body, fontsize=10)


def _add_image_page(doc: fitz.Document) -> None:
    """Pagina interamente raster, zero testo -> grafica (HEAVY)."""
    page = doc.new_page()
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 240, 240), False)
    pix.clear_with(128)
    page.insert_image(page.rect, stream=pix.tobytes("png"))


def _add_vector_page(doc: fitz.Document) -> None:
    """Pagina a densa carica vettoriale (esploso): >20k segmenti -> grafica (HEAVY)."""
    page = doc.new_page()
    pts = [(10 + (n % 500), 10 + (n // 500)) for n in range(20050)]
    page.draw_polyline(pts, width=0.2)


def _write(doc: fitz.Document, path) -> str:
    doc.save(str(path))
    doc.close()
    return str(path)


# --------------------------------------------------------------- unit: route_for


class TestRouteFor:
    def test_born_digital_solo_testo_e_fast(self):
        pc = PageClass("testuale", False, False, False, "text-layer ricco")
        assert route_for(pc) is Route.FAST_TEXT

    def test_pagina_mista_dual_e_heavy(self):
        pc = PageClass("testuale", True, False, False, "mista")
        assert route_for(pc) is Route.HEAVY

    def test_scansione_muta_e_heavy(self):
        pc = PageClass("testuale", False, True, False, "senza text-layer")
        assert route_for(pc) is Route.HEAVY

    def test_pagina_vuota_e_heavy(self):
        pc = PageClass("testuale", False, False, True, "quasi vuota")
        assert route_for(pc) is Route.HEAVY

    def test_grafica_e_heavy(self):
        pc = PageClass("grafica", False, False, False, "esploso")
        assert route_for(pc) is Route.HEAVY


# --------------------------------------------------------- integrazione: dispatch


class TestDispatchPdf:
    def test_pagina_testo_e_fast_ed_estrae(self, tmp_path):
        doc = fitz.open()
        _add_text_page(doc)
        path = _write(doc, tmp_path / "solo_testo.pdf")

        res = dispatch_pdf(path)

        assert isinstance(res, DocumentDispatch)
        assert res.n_pages == 1
        assert res.fast_pages == 1 and res.heavy_pages == 0
        assert res.born_digital is True
        page = res.pages[0]
        assert isinstance(page, PageDispatch)
        assert page.route is Route.FAST_TEXT
        assert page.text is not None and "AB-12345-X" in page.text

    def test_pagina_immagine_e_heavy_senza_testo(self, tmp_path):
        doc = fitz.open()
        _add_image_page(doc)
        path = _write(doc, tmp_path / "immagine.pdf")

        res = dispatch_pdf(path)

        assert res.fast_pages == 0 and res.heavy_pages == 1
        assert res.pages[0].route is Route.HEAVY
        assert res.pages[0].text is None

    def test_pagina_vettoriale_densa_e_heavy(self, tmp_path):
        doc = fitz.open()
        _add_vector_page(doc)
        path = _write(doc, tmp_path / "esploso.pdf")

        res = dispatch_pdf(path)

        assert res.heavy_pages == 1
        assert res.pages[0].route is Route.HEAVY

    def test_pdf_misto_split_per_pagina(self, tmp_path):
        doc = fitz.open()
        _add_text_page(doc)   # p1 -> FAST_TEXT
        _add_image_page(doc)  # p2 -> HEAVY
        path = _write(doc, tmp_path / "misto.pdf")

        res = dispatch_pdf(path)

        assert res.n_pages == 2
        assert [p.route for p in res.pages] == [Route.FAST_TEXT, Route.HEAVY]
        assert res.fast_pages == 1 and res.heavy_pages == 1
        assert res.born_digital is True
        assert res.pages[0].text is not None and res.pages[1].text is None

    def test_extract_text_false_non_estrae(self, tmp_path):
        doc = fitz.open()
        _add_text_page(doc)
        path = _write(doc, tmp_path / "dry_run.pdf")

        res = dispatch_pdf(path, extract_text=False)

        assert res.pages[0].route is Route.FAST_TEXT  # classifica comunque
        assert res.pages[0].text is None              # ma non estrae


# ----------------------------------------------------- fail-closed: errori rumorosi


class TestFailClosed:
    def test_pdf_cifrato_solleva(self, tmp_path):
        doc = fitz.open()
        _add_text_page(doc)
        path = tmp_path / "cifrato.pdf"
        doc.save(str(path), encryption=fitz.PDF_ENCRYPT_AES_256,
                 owner_pw="owner", user_pw="secret")
        doc.close()

        with pytest.raises(EncryptedPdfError):
            dispatch_pdf(str(path))

    def test_file_corrotto_solleva(self, tmp_path):
        path = tmp_path / "rotto.pdf"
        path.write_bytes(b"%PDF-1.7 questo non e' un PDF valido \x00\x01\x02")

        with pytest.raises(CorruptPdfError):
            dispatch_pdf(str(path))

    def test_file_mancante_solleva(self, tmp_path):
        with pytest.raises(CorruptPdfError):
            dispatch_pdf(str(tmp_path / "inesistente.pdf"))


# --------------------------------------------------------------- wrapper async


class TestAsyncWrapper:
    def test_async_ritorna_stesso_esito(self, tmp_path):
        doc = fitz.open()
        _add_text_page(doc)
        path = _write(doc, tmp_path / "async_ok.pdf")

        res = asyncio.run(dispatch_pdf_async(path))

        assert res.fast_pages == 1
        assert res.pages[0].route is Route.FAST_TEXT

    def test_async_propaga_eccezione(self, tmp_path):
        with pytest.raises(CorruptPdfError):
            asyncio.run(dispatch_pdf_async(str(tmp_path / "manca.pdf")))
