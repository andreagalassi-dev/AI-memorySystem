"""Fase 1.2 — Estrattore testuale block-aware per tabelle born-digital.

Le pagine-tabella CON text-layer nativo affidabile NON devono passare da GLM-OCR
(il VLM corrompe i codici alfanumerici: `V09011500C01` -> `V0901150001`). Questo
estrattore legge le coordinate delle parole (PyMuPDF `get_text("words")`) e
ricostruisce le righe per BLOCCHI di colonne, garantendo codici ESATTI al 100%
(nessuna inferenza: il codice e' il token nativo verbatim).

Test DB-free e (per l'integrazione) skippabili se manca fitz o il corpus.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.ingestion.extraction.text_table import (
    DEFAULT_CODE_RE,
    TextTableExtractor,
    reconstruct_rows,
    reliable_text_layer,
)
from src.ingestion.extraction.extractor import PageRef, TableExtractor


def _w(x0, y0, text):
    """Costruisce una tupla-parola compatibile con PyMuPDF get_text('words')."""
    return (float(x0), float(y0), float(x0) + 40, float(y0) + 8, text, 0, 0, 0)


# --------------------------------------------------------------- reconstruct_rows


def test_singolo_blocco_ricostruisce_codice_desc_prezzo():
    """Una riga born-digital -> RigaGrezza con codice esatto, descrizione e prezzo."""
    words = [
        _w(28, 96, "V09008000001"),
        _w(88, 99, "Rosone/Wall"), _w(150, 99, "plate"), _w(200, 99, "Stylos"),
        _w(243, 99, "1"),
        _w(265, 99, "90,00"),
    ]
    righe, rejects = reconstruct_rows(words)
    assert len(righe) == 1
    r = righe[0]
    assert r.codice_originale == "V09008000001"      # esatto, verbatim
    assert "Rosone" in r.descrizione and "Stylos" in r.descrizione
    assert r.prezzo == Decimal("90.00")
    assert rejects == []


def test_due_blocchi_niente_bleed_tra_colonne():
    """Blocchi affiancati: la riga del blocco sinistro non pesca parole del destro."""
    words = [
        # blocco 1 (x~28)
        _w(28, 96, "V09008000001"), _w(88, 99, "Rosone"), _w(265, 99, "90,00"),
        # blocco 2 (x~312), STESSA banda-y
        _w(312, 96, "V09039700C01"), _w(371, 99, "Snodo"), _w(549, 99, "31,00"),
    ]
    righe, _ = reconstruct_rows(words)
    by_code = {r.codice_originale: r for r in righe}
    assert set(by_code) == {"V09008000001", "V09039700C01"}
    assert by_code["V09008000001"].prezzo == Decimal("90.00")
    assert "Rosone" in by_code["V09008000001"].descrizione
    assert "Snodo" not in by_code["V09008000001"].descrizione   # niente bleed
    assert by_code["V09039700C01"].prezzo == Decimal("31.00")


def test_codice_senza_dati_va_in_reject_non_emesso():
    """Un token-codice isolato (header/rumore) non deve produrre una riga fabbricata."""
    words = [_w(28, 96, "V09008000001")]  # solo il codice, nessun contorno
    righe, rejects = reconstruct_rows(words)
    assert righe == []
    assert len(rejects) == 1
    assert rejects[0]["codice"] == "V09008000001"


def test_prezzo_mancante_emette_codice_e_desc_prezzo_none():
    """Riga con codice+descrizione ma senza prezzo: emessa con prezzo None (codice esatto)."""
    words = [
        _w(28, 96, "V09010000007"),
        _w(88, 99, "Accessori"), _w(150, 99, "fissaggio"),
    ]
    righe, rejects = reconstruct_rows(words)
    assert len(righe) == 1
    assert righe[0].codice_originale == "V09010000007"
    assert righe[0].prezzo is None
    assert "Accessori" in righe[0].descrizione


def test_lettere_nel_codice_preservate_non_confuse_con_zero():
    """La C/G interne al codice (che GLM-OCR perde) restano verbatim."""
    words = [
        _w(28, 96, "V09011500C01"), _w(88, 99, "Flessibile"), _w(265, 99, "23,50"),
        _w(28, 111, "V09012000G01"), _w(88, 114, "Leva"), _w(265, 111, "193,50"),
    ]
    righe, _ = reconstruct_rows(words)
    codici = {r.codice_originale for r in righe}
    assert "V09011500C01" in codici    # la C non diventa 0
    assert "V09012000G01" in codici    # la G non diventa 0


def test_code_re_non_confonde_numeri_modello_o_header():
    """Il rilevatore di codici esclude header (CODICE), prezzi (90,00) e numeri (2817)."""
    assert DEFAULT_CODE_RE.match("V09008000001")
    assert DEFAULT_CODE_RE.match("V09011500C01")
    assert not DEFAULT_CODE_RE.match("CODICE")     # nessuna cifra
    assert not DEFAULT_CODE_RE.match("2817")       # nessuna lettera
    assert not DEFAULT_CODE_RE.match("90,00")      # separatore non alfanumerico


# --------------------------------------------------------------- reliable_text_layer


class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def get_text(self, kind: str) -> str:
        return self._text


def test_reliable_text_layer_vero_su_pagina_testuale():
    assert reliable_text_layer(_FakePage("x" * 200)) is True


def test_reliable_text_layer_falso_su_scansione_muta():
    assert reliable_text_layer(_FakePage("   ")) is False   # <50 char -> needs_ocr


# --------------------------------------------------------------- Protocol


def test_text_table_extractor_e_un_tableextractor():
    """L'estrattore rispetta il Port TableExtractor (drop-in nel runner)."""
    assert isinstance(TextTableExtractor(doc=None), TableExtractor)


# --------------------------------------------------------------- integrazione reale


_MAMOLI = Path("corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf")


@pytest.mark.skipif(not _MAMOLI.exists(), reason="corpus Mamoli non disponibile")
def test_integrazione_p230_codici_esatti_zero_corruzione():
    """PROVA ANTI-CORRUZIONE: sul vero listino p230 i codici escono verbatim.

    E' il difetto che ha motivato il fix: GLM-OCR restituiva V0901150001 (11 char,
    C persa); l'estrattore testuale deve restituire V09011500C01 (12 char, C intatta).
    """
    fitz = pytest.importorskip("fitz")
    doc = fitz.open(str(_MAMOLI))
    ex = TextTableExtractor(doc=doc)
    righe = ex.extract(PageRef("Mamoli", 230))
    codici = {r.codice_originale for r in righe}

    # copertura: la pagina ha ~120 righe su 4 blocchi
    assert len(righe) >= 80, f"troppe poche righe ricostruite: {len(righe)}"
    # codici ESATTI con lettere interne preservate (cio' che GLM-OCR sbagliava)
    assert "V09011500C01" in codici
    assert "V09012000G01" in codici
    assert "V09008000001" in codici
    # nessun codice emesso deve essere corrotto: tutti matchano il rilevatore
    assert all(DEFAULT_CODE_RE.match(c) for c in codici)
    # prezzo di una riga nota (V09008000001 = 90,00)
    r0 = next(r for r in righe if r.codice_originale == "V09008000001")
    assert r0.prezzo == Decimal("90.00")
    doc.close()
