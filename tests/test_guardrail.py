"""P3.1 — Test del guardrail deterministico sui codici (NON-LLM).

Fixture avversario a 26 casi (docs/superpowers/specs/2026-07-03-p31-guardrail-
codici-design.md §5): quasi-giusti che devono essere BLOCCATI, varianti-
separatore che vanno a VERIFICA MANUALE, misure/date che NON devono essere
estratte, codici reali che devono risultare ESISTENTI (nessun falso blocco).

Regola d'oro sotto test: giusto oppure "non lo so", mai sbagliato in silenzio.
"""

from __future__ import annotations

from src.retrieval.guardrail import (
    check_answer,
    extract_candidates,
    is_measurement,
    verify_code,
)

# Codici REALI (forma ancorata al corpus: V0 Mamoli, 0US Electrolux, num-9).
REAL_V0 = "V09156800007"
REAL_0US = "0USBX4"
REAL_NUM9 = "340006734"


def ev(*codes, status="clean", warnings=None):
    """Costruisce un evidence set (payload dei chunk recuperati)."""
    return [{"code": c, "doc": "Mamoli_Ricambi", "status": status,
             "warnings": warnings or []} for c in codes]


def cat(*present):
    """Fake catalog_lookup: True se la chiave letterale e' nel set dato."""
    s = set(present)
    return lambda key: key in s


# ---------------------------------------------------------------- Stadio C: verify_code

class TestVerdettoEsiste:
    def test_identita(self):  # #1
        assert verify_code(REAL_V0, [REAL_V0], cat()).status == "EXISTS"

    def test_solo_case(self):  # #2
        assert verify_code(REAL_V0.lower(), [REAL_V0], cat()).status == "EXISTS"

    def test_markup(self):  # #3
        assert verify_code(f"**{REAL_V0}**", [REAL_V0], cat()).status == "EXISTS"

    def test_rejoin_spazi(self):  # #4
        assert verify_code("V0 91568 00007", [REAL_V0], cat()).status == "EXISTS"

    def test_identita_0us(self):  # #8
        assert verify_code(REAL_0US, [REAL_0US], cat()).status == "EXISTS"

    def test_identita_num9(self):  # #12
        assert verify_code(REAL_NUM9, [REAL_NUM9], cat()).status == "EXISTS"


class TestVerdettoBlocco:
    def test_sostituzione_cifra(self):  # #5
        assert verify_code("V09156800008", [REAL_V0], cat()).status == "ABSENT"

    def test_trasposizione(self):  # #6
        assert verify_code("V09156800070", [REAL_V0], cat()).status == "ABSENT"

    def test_fabbricato_non_a_catalogo(self):  # #7
        assert verify_code("V0ZZZZZZZZZZ", [REAL_V0], cat()).status == "ABSENT"

    def test_sostituzione_0us(self):  # #9
        assert verify_code("0USBX5", [REAL_0US], cat()).status == "ABSENT"

    def test_ocr_o_vs_zero(self):  # #10 — lettera O != cifra 0, mai foldare
        assert verify_code("OUSBX4", [REAL_0US], cat()).status == "ABSENT"

    def test_troncamento_ed_extra(self):  # #11
        assert verify_code("0USBX", [REAL_0US], cat()).status == "ABSENT"
        assert verify_code("0USBX44", [REAL_0US], cat()).status == "ABSENT"

    def test_sostituzione_num9(self):  # #14
        assert verify_code("340006735", [REAL_NUM9], cat()).status == "ABSENT"


class TestVerdettoVerificaManuale:
    def test_variante_separatore_numerico(self):  # #13
        v = verify_code("340-006-734", [REAL_NUM9], cat())
        assert v.status == "AMBIGUOUS" and v.matched_evidence_code == REAL_NUM9

    def test_separatore_significativo_corto(self):  # #15
        assert verify_code("M6-16", ["M616"], cat()).status == "AMBIGUOUS"

    def test_separatore_simmetrico(self):  # #16
        assert verify_code("M616", ["M6-16"], cat()).status == "AMBIGUOUS"

    def test_miss_retrieval(self):  # #17 — assente in evidenza ma in catalogo
        v = verify_code(REAL_V0, [REAL_0US], cat(REAL_V0))
        assert v.status == "MISS_RETRIEVAL"


class TestFailClosed:
    def test_lookup_solleva_va_a_verifica(self):
        def boom(_key):
            raise RuntimeError("qdrant giu'")
        v = verify_code("V0ZZZZZZZZZZ", [REAL_V0], boom)
        assert v.status == "MISS_RETRIEVAL"  # mai ABSENT/PASS silenzioso su errore

    def test_reason_sempre_presente(self):
        for cand, evc in [(REAL_V0, [REAL_V0]), ("V0ZZZZZZZZZZ", [REAL_V0]),
                          ("M6-16", ["M616"])]:
            assert verify_code(cand, evc, cat()).reason.strip()


# ---------------------------------------------------------------- Stadio A: estrazione

class TestEstrazioneCodici:
    def test_codici_semplici(self):
        got = extract_candidates(f"il ricambio {REAL_V0} e il {REAL_0US}")
        assert got == [REAL_V0, REAL_0US]

    def test_rejoin_un_solo_candidato(self):  # #4 lato estrazione
        assert extract_candidates("codice V0 91568 00007 grazie") == ["V0 91568 00007"]

    def test_doppio_fuso(self):  # #18
        got = extract_candidates("V0AAAA1111BBV0CCCC2222DD")
        assert len(got) == 2

    def test_numerico_con_separatori_estratto(self):  # #13 lato estrazione
        assert extract_candidates("vedi 340-006-734 a listino") == ["340-006-734"]

    def test_famiglia_batte_il_filtro_misura(self):  # #26
        # 0USBXV finisce in V ma matcha la famiglia 0US -> resta candidato
        assert "0USBXV" in extract_candidates("il pezzo 0USBXV")

    def test_troncamento_famiglia_non_maschera(self):
        # 0USBX44: lo span famiglia '0USBX4' NON deve troncare via il '4' extra,
        # mascherando un'allucinazione -> si tiene il token intero.
        assert extract_candidates("il pezzo 0USBX44") == ["0USBX44"]


class TestNonEstrareMisure:
    def test_coppia_e_frazione(self):  # #21
        assert extract_candidates("coppia 12 Nm, attacco 1/2 pollice") == []

    def test_range_tensione(self):  # #22
        assert extract_candidates("alimentazione 12-24V DC") == []

    def test_filetto_dimensionale(self):  # #23
        assert extract_candidates("vite M6x16 a brugola") == []

    def test_unita_varie(self):  # #24
        assert extract_candidates("diametro 5mm, 50Hz, 230V") == []

    def test_data(self):  # #25
        assert extract_candidates("revisione del 12/05/2024") == []


class TestIsMeasurement:
    def test_unita(self):
        for t in ["230V", "12-24V", "5mm", "50mm", "50Hz", "10bar"]:
            assert is_measurement(t), t

    def test_dimensionale_e_data(self):
        assert is_measurement("M6x16") and is_measurement("12x16")
        assert is_measurement("12/05/2024") and is_measurement("2024-05-12")

    def test_codici_non_sono_misure(self):
        for t in [REAL_V0, REAL_0US, REAL_NUM9, "0USBXV", "M616"]:
            assert not is_measurement(t), t


# ---------------------------------------------------------------- Gate: check_answer

class TestGateAggregazione:
    def test_tutti_esistono_pass(self):
        r = check_answer(f"monta {REAL_V0} e {REAL_0US}",
                         ev(REAL_V0, REAL_0US), cat())
        assert r.gate == "PASS"

    def test_un_allucinato_blocca(self):  # peggiore-vince
        r = check_answer(f"usa {REAL_V0} e V0ZZZZZZZZZZ", ev(REAL_V0), cat())
        assert r.gate == "BLOCK"

    def test_variante_separatore_verifica(self):  # #13 a livello gate
        r = check_answer("il codice e' 340-006-734", ev(REAL_NUM9), cat())
        assert r.gate == "VERIFY"

    def test_nessun_codice_pass(self):  # #19
        r = check_answer("La guarnizione va sostituita ogni anno.", ev(REAL_V0), cat())
        assert r.gate == "PASS"

    def test_evidenza_degradata_verifica(self):  # #20 — gancio #3
        r = check_answer(f"vedi {REAL_V0}", ev(REAL_V0, status="degraded"), cat())
        assert r.gate == "VERIFY"

    def test_evidenza_ocr_source_verifica(self):  # #20 bis
        r = check_answer(f"vedi {REAL_V0}", ev(REAL_V0, warnings=["ocr_source"]), cat())
        assert r.gate == "VERIFY"

    def test_allucinazione_con_char_extra_blocca(self):
        r = check_answer("monta 0USBX44", ev(REAL_0US), cat())
        assert r.gate == "BLOCK"

    def test_misure_non_bloccano(self):  # #21-25 a livello gate
        r = check_answer("alimentazione 12-24V, vite M6x16, del 12/05/2024",
                         ev(REAL_V0), cat())
        assert r.gate == "PASS"
