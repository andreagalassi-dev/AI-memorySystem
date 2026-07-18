"""Fase 1.2 — Test dei contratti Pydantic e del port/FakeExtractor.

Strato FORMA (RigaGrezza): campi inventati, mancanti, vuoti, prezzo negativo ->
ValidationError. Arricchimento (RigaListino.from_grezza): il codice_normalizzato e'
calcolato da noi, non dal modello. Port: FakeExtractor deterministico e isolato.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.ingestion.codes import canonical
from src.ingestion.extraction.extractor import FakeExtractor, PageRef, TableExtractor
from src.ingestion.extraction.schema import (
    MotivoScarto,
    RigaGrezza,
    RigaListino,
    Scarto,
)


# ------------------------------------------------------- strato FORMA: RigaGrezza


class TestRigaGrezza:
    def test_riga_valida(self):
        r = RigaGrezza(codice_originale="V0-123", descrizione="Valvola", prezzo="12.50")
        assert r.codice_originale == "V0-123"
        assert r.prezzo == Decimal("12.50")

    def test_prezzo_e_descrizione_opzionali(self):
        r = RigaGrezza(codice_originale="AB-1")
        assert r.prezzo is None and r.descrizione is None

    def test_campo_inventato_rifiutato(self):
        # extra="forbid": il modello NON puo' aggiungere campi (anti-invenzione struttura)
        with pytest.raises(ValidationError):
            RigaGrezza(codice_originale="V0-123", sconto=0.2)

    def test_codice_mancante_rifiutato(self):
        with pytest.raises(ValidationError):
            RigaGrezza(descrizione="senza codice")

    def test_codice_vuoto_rifiutato(self):
        with pytest.raises(ValidationError):
            RigaGrezza(codice_originale="")

    def test_codice_soli_spazi_rifiutato(self):
        with pytest.raises(ValidationError):
            RigaGrezza(codice_originale="   ")

    def test_prezzo_negativo_rifiutato(self):
        with pytest.raises(ValidationError):
            RigaGrezza(codice_originale="V0-123", prezzo="-1")


# ------------------------------------------------- arricchimento: RigaListino


class TestRigaListino:
    def test_from_grezza_normalizza_noi(self):
        grezza = RigaGrezza(codice_originale="v0 123", prezzo="9")
        riga = RigaListino.from_grezza(grezza, doc_stem="listino_x", page=3)
        assert riga.codice_normalizzato == canonical("v0 123")
        assert riga.doc_stem == "listino_x" and riga.page == 3
        assert riga.codice_originale == "v0 123"  # l'originale resta intatto

    def test_normalizzato_indipendente_dal_modello(self):
        # due grezze con lo stesso codice in forme diverse -> stesso normalizzato
        a = RigaListino.from_grezza(RigaGrezza(codice_originale="AB-12-X"),
                                    doc_stem="d", page=1)
        b = RigaListino.from_grezza(RigaGrezza(codice_originale="ab 12 x"),
                                    doc_stem="d", page=1)
        assert a.codice_normalizzato == b.codice_normalizzato


# ------------------------------------------------------------------- Scarto


class TestScarto:
    def test_scarto_minimo(self):
        s = Scarto(doc_stem="d", page=2, motivo=MotivoScarto.CODICE_NON_ANCORATO)
        assert s.motivo == "codice_non_ancorato"
        assert s.codice_originale is None


# ------------------------------------------------------------- port / Fake


class TestFakeExtractor:
    def test_e_un_table_extractor(self):
        assert isinstance(FakeExtractor(), TableExtractor)

    def test_ritorna_righe_programmate(self):
        rows = [RigaGrezza(codice_originale="V0-1"), RigaGrezza(codice_originale="V0-2")]
        fake = FakeExtractor({("listino", 5): rows})
        out = fake.extract(PageRef("listino", 5))
        assert [r.codice_originale for r in out] == ["V0-1", "V0-2"]

    def test_pagina_non_programmata_e_vuota(self):
        fake = FakeExtractor({("listino", 5): [RigaGrezza(codice_originale="V0-1")]})
        assert fake.extract(PageRef("listino", 99)) == []
        assert fake.extract(PageRef("altro", 5)) == []

    def test_output_e_copia_difensiva(self):
        rows = [RigaGrezza(codice_originale="V0-1")]
        fake = FakeExtractor({("d", 1): rows})
        out = fake.extract(PageRef("d", 1))
        out.append(RigaGrezza(codice_originale="INTRUSO"))
        # la mutazione del risultato non intacca lo stato del fake
        assert len(fake.extract(PageRef("d", 1))) == 1
