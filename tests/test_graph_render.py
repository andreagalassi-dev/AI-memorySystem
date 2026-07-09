"""Fase 1.3 Passo 4 — Test delle funzioni PURE di render (prompt + value-grounding).

Due responsabilita', entrambe DB-free e LLM-free:
  - `build_render_prompt`: serializza i dati GIA' letti dal DB per l'LLM. Deve
    contenere SOLO i valori (nessun SQL) e la domanda, con grounding confinato.
  - `verify_price_grounding`: barriera #2 deterministica. Estrae i numeri-PREZZO
    dalla risposta dell'LLM (ignorando conteggi come "1 risultato", numeri di pagina
    e le cifre interne ai codici), li normalizza (virgole/punti/valuta) e pretende
    che ogni prezzo enunciato corrisponda a un valore del DB (nessuna allucinazione)
    e che almeno un valore del DB sia effettivamente enunciato (grounding positivo).
"""

from __future__ import annotations

from decimal import Decimal

from src.graph.render import (
    SYSTEM_RENDER,
    build_render_prompt,
    verify_price_grounding,
)
from src.graph.tools.sql_tool import LookupRow


def _rows(*vals, doc="listino_x", page=3):
    return [LookupRow(valore=v, doc_stem=doc, page=page) for v in vals]


class TestSystemRender:
    def test_vieta_invenzione_e_impone_refusal(self):
        assert "NON inventare" in SYSTEM_RENDER or "non inventare" in SYSTEM_RENDER.lower()
        # deve imporre il rifiuto testuale esatto della sintesi (coerenza col grafo)
        from src.retrieval.synthesis import REFUSAL
        assert REFUSAL in SYSTEM_RENDER

    def test_vieta_sql(self):
        assert "SQL" in SYSTEM_RENDER  # la regola "non generare SQL" è esplicita


class TestBuildRenderPrompt:
    def test_contiene_valori_e_provenienza_non_sql(self):
        p = build_render_prompt("quanto costa V0-123", "V0-123", "prezzo",
                                _rows(Decimal("12.50")))
        assert "12.50" in p and "listino_x" in p and "3" in p
        assert "V0-123" in p and "prezzo" in p
        assert "quanto costa V0-123" in p
        # nessuna forma di SQL nel prompt dato all'LLM
        for banned in ("SELECT", "select", "%s", "righe_listino"):
            assert banned not in p


class TestPriceGroundingHappyPath:
    def test_prezzo_decimale_con_valuta_ok(self):
        assert verify_price_grounding("Il prezzo è 12,50 €.", [Decimal("12.50")]) is True

    def test_prezzo_con_punto_ok(self):
        assert verify_price_grounding("Prezzo: 12.50", [Decimal("12.50")]) is True

    def test_intero_con_valuta_ok(self):
        # 100 senza decimali ma con simbolo di valuta = prezzo
        assert verify_price_grounding("Costa 100 €.", [Decimal("100.00")]) is True


class TestPriceGroundingCasiInsidiosi:
    def test_ignora_conteggio_uno_risultato(self):
        # il caso citato dall'utente: "1 risultato" NON è un prezzo allucinato
        ans = "Ho trovato 1 risultato per questo codice. Il prezzo è 15,50 €."
        assert verify_price_grounding(ans, [Decimal("15.50")]) is True

    def test_ignora_conteggio_e_pagina(self):
        ans = "Trovati 2 record (listino_x, pag. 3). Prezzo 15,50 €."
        assert verify_price_grounding(ans, [Decimal("15.50")]) is True

    def test_ignora_cifre_interne_al_codice(self):
        # le cifre di V09199300001 non devono essere lette come prezzi
        ans = "Il codice V09199300001 ha prezzo 15,50 €."
        assert verify_price_grounding(ans, [Decimal("15.50")]) is True

    def test_migliaia_europeo_punto_migliaia_virgola_decimali(self):
        assert verify_price_grounding("Prezzo 1.234,56 €.", [Decimal("1234.56")]) is True

    def test_migliaia_us_virgola_migliaia_punto_decimali(self):
        assert verify_price_grounding("Price 1,234.56 €.", [Decimal("1234.56")]) is True

    def test_tolleranza_decimali_troncati(self):
        # DB 12.50, LLM scrive 12,5 -> uguaglianza numerica Decimal
        assert verify_price_grounding("Prezzo 12,5 €.", [Decimal("12.50")]) is True


class TestPriceGroundingFailClosed:
    def test_prezzo_allucinato_blocca(self):
        assert verify_price_grounding("Il prezzo è 999,00 €.", [Decimal("12.50")]) is False

    def test_prezzo_extra_oltre_a_quello_giusto_blocca(self):
        # cita quello giusto MA aggiunge uno inventato -> fail-closed
        ans = "Prezzo 12,50 €, oppure 80,00 € in offerta."
        assert verify_price_grounding(ans, [Decimal("12.50")]) is False

    def test_nessun_prezzo_enunciato_blocca(self):
        # grounding POSITIVO: se non enuncia alcun prezzo, non conferma nulla
        assert verify_price_grounding("Il codice esiste a catalogo.", [Decimal("12.50")]) is False

    def test_prezzo_scritto_come_intero_nudo_non_conferma(self):
        # "15" nudo (senza valuta né decimali) è indistinguibile da un conteggio:
        # il valore DB non risulta enunciato -> fail-closed
        assert verify_price_grounding("Il prezzo è 15.", [Decimal("15.50")]) is False

    def test_db_senza_valore_numerico_blocca(self):
        # codice trovato ma prezzo NULL a DB -> niente da confermare -> fail-closed
        assert verify_price_grounding("Prezzo 12,50 €.", [None]) is False
