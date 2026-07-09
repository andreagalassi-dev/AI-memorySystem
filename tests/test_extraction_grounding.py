"""Fase 1.2 — Test dello strato 2 Fail-Closed (grounding verbatim).

Il codice allucinato (non fisicamente presente nel testo grezzo) va in cortocircuito
nella tabella Scarti; il codice presente — anche come variante-separatore — passa.
Include la dimostrazione end-to-end col FakeExtractor che emette un codice fabbricato.
"""

from __future__ import annotations

from decimal import Decimal

from src.ingestion.extraction.extractor import FakeExtractor, PageRef
from src.ingestion.extraction.grounding import (
    is_grounded,
    page_code_set,
    partition,
)
from src.ingestion.extraction.schema import MotivoScarto, RigaGrezza, RigaListino

# Testo grezzo di una pagina-listino: contiene V0-123 e 340-006-734 (col separatore).
PAGE = (
    "LISTINO RICAMBI 2026\n"
    "V0-123   Valvola di pressione        12,50\n"
    "340-006-734  Guarnizione OR          3,20\n"
)


def _riga(codice: str, *, prezzo: str | None = None,
          doc_stem: str = "listino_x", page: int = 1) -> RigaListino:
    grezza = RigaGrezza(codice_originale=codice, prezzo=prezzo)
    return RigaListino.from_grezza(grezza, doc_stem=doc_stem, page=page)


# ------------------------------------------------------------- page_code_set


class TestPageCodeSet:
    def test_estrae_gli_skeleton_presenti(self):
        codes = page_code_set(PAGE)
        assert "V0123" in codes
        assert "340006734" in codes

    def test_nessun_codice_ricambio_su_prosa(self):
        # indice di presenza: la prosa produce skeleton di parole, ma NON gli
        # skeleton dei codici ricambio (che restano non-ancorabili)
        codes = page_code_set("Introduzione. Avvertenze di sicurezza generali.")
        assert "V0123" not in codes and "340006734" not in codes


# ------------------------------------------------------------- is_grounded


class TestIsGrounded:
    def test_presente(self):
        assert is_grounded("V0123", {"V0123", "340006734"}) is True

    def test_assente(self):
        assert is_grounded("V0999", {"V0123", "340006734"}) is False

    def test_skeleton_vuoto_mai_ancorato(self):
        assert is_grounded("", {"V0123"}) is False


# ------------------------------------------------------------- partition


class TestPartition:
    def test_codice_presente_passa(self):
        valide, scarti = partition([_riga("V0-123", prezzo="12.50")], PAGE)
        assert len(valide) == 1 and scarti == []
        assert valide[0].codice_normalizzato == "V0123"
        assert valide[0].prezzo == Decimal("12.50")

    def test_variante_separatore_passa(self):
        # il modello emette '340006734'; la pagina ha '340-006-734' (stesso skeleton)
        valide, scarti = partition([_riga("340006734")], PAGE)
        assert len(valide) == 1 and scarti == []

    def test_codice_allucinato_scartato(self):
        valide, scarti = partition([_riga("V0-999", prezzo="99.00")], PAGE)
        assert valide == []
        assert len(scarti) == 1
        s = scarti[0]
        assert s.motivo == MotivoScarto.CODICE_NON_ANCORATO
        assert s.codice_originale == "V0-999"
        assert s.codice_normalizzato_tentato == "V0999"
        assert s.payload_raw["prezzo"] == "99.00"  # audit del valore inventato

    def test_mix_valide_e_scarti(self):
        rows = [_riga("V0-123"), _riga("XX-0001"), _riga("340-006-734")]
        valide, scarti = partition(rows, PAGE)
        assert {r.codice_normalizzato for r in valide} == {"V0123", "340006734"}
        assert [s.codice_normalizzato_tentato for s in scarti] == ["XX0001"]

    def test_pagina_muta_scarta_tutto(self):
        # nessun codice nel testo -> ogni riga estratta e' per definizione infondata
        rows = [_riga("V0-123"), _riga("340-006-734")]
        valide, scarti = partition(rows, "Pagina di sole avvertenze, nessun codice.")
        assert valide == []
        assert len(scarti) == 2


# ------------------------------------------- Fail-Closed end-to-end col Fake


class TestFailClosedEndToEnd:
    def test_fake_emette_codice_fabbricato_va_negli_scarti(self):
        # il modello (fake) inventa 'V0-777' su una pagina che NON lo contiene
        fake = FakeExtractor({
            ("listino_x", 1): [
                RigaGrezza(codice_originale="V0-123", prezzo="12.50"),  # reale
                RigaGrezza(codice_originale="V0-777", prezzo="88.00"),  # allucinato
            ]
        })
        grezze = fake.extract(PageRef("listino_x", 1))
        righe = [RigaListino.from_grezza(g, doc_stem="listino_x", page=1) for g in grezze]

        valide, scarti = partition(righe, PAGE)

        assert [r.codice_normalizzato for r in valide] == ["V0123"]
        assert len(scarti) == 1
        assert scarti[0].codice_originale == "V0-777"
        assert scarti[0].motivo == MotivoScarto.CODICE_NON_ANCORATO
