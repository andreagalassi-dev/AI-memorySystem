"""P3.1 — Test del guardrail deterministico sui codici (NON-LLM).

Fixture avversario a 26 casi (docs/superpowers/specs/2026-07-03-p31-guardrail-
codici-design.md §5): quasi-giusti che devono essere BLOCCATI, varianti-
separatore che vanno a VERIFICA MANUALE, misure/date che NON devono essere
estratte, codici reali che devono risultare ESISTENTI (nessun falso blocco).

Regola d'oro sotto test: giusto oppure "non lo so", mai sbagliato in silenzio.
"""

from __future__ import annotations

from src.retrieval.guardrail import (
    _literal_key,
    check_answer,
    extract_candidates,
    is_measurement,
    verify_code,
)
from src.retrieval.synthesis import extract_citations, strip_citations

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

    def test_prezzo_alto_non_e_codice(self):  # #27 — bug prezzo>=100 (eval ampio B)
        # Il prezzo 273.52 (canonical->27352, 5 cifre) non deve essere estratto
        # come codice accanto al codice reale citato dall'LLM.
        assert extract_candidates(
            "Il codice 2311KJ ha un prezzo di 273.52 EUR") == ["2311KJ"]

    def test_prezzo_nudo_non_estratto(self):  # #27 bis — vari formati prezzo
        for s in ["prezzo 3475.78 EUR", "costa 141,98 euro", "totale 1234.50"]:
            assert extract_candidates(s) == [], s


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

    def test_prezzi_decimali_sono_misure(self):  # bug prezzo>=100
        for t in ["273.52", "3475.78", "141,98", "18.35", "1.42", "9.5"]:
            assert is_measurement(t), t

    def test_codici_puri_cifre_non_sono_misure(self):  # regressione: no falso-positivo
        # Un codice puro-cifre o a gruppi NON deve diventare una "misura".
        for t in ["27352", "10023", "340006734", "00000013"]:
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

    def test_prezzo_alto_non_blocca(self):  # bug prezzo>=100 a livello gate
        # Codice reale in evidenza + prezzo >=100: deve PASSARE, non BLOCK.
        r = check_answer("Il codice 2311KJ ha un prezzo di 273.52 EUR",
                         ev("2311KJ"), cat())
        assert r.gate == "PASS"


# ------------------------------------------ EXISTS_IN_TEXT: token nella DESCRIZIONE
# SPEC §D chiede che il codice citato "esista letteralmente nella tabella
# recuperata": la DESCRIZIONE fa parte della riga recuperata. L'LLM cita
# verbatim la descrizione, che contiene token code-shaped non-part-number
# (rimandi, misure, sigle). Il gradino EXISTS_IN_TEXT li riconosce SENZA
# indebolire il guardrail: estrazione SIMMETRICA (mai `in` su stringa) e PASS
# solo se la (doc, page) della riga sta fra le citazioni della risposta.

DOC = "rotax_912_914_IPC"
C005_DESC = "CABLE SUPPORT ASSY. For cylinder 2 and 4 SI-912-012 SI-914-014"


def evd(code, desc=None, text=None, doc=DOC, page=79, status="clean", warnings=None):
    """Evidence row completa (con doc/page/desc), come i payload reali."""
    return {"code": code, "desc": desc, "text": text, "doc": doc, "page": page,
            "status": status, "warnings": warnings or []}


CITE_79 = [(DOC, 79)]

# Risposta REALE misurata sul corpus (e2e `answer("codice 961267", "tecnico")`).
C005_ANSWER = ("Il codice 961267 è un CABLE SUPPORT ASSY. per i cilindri 2 e 4 "
               "SI-912-012 SI-914-014 (rotax_912_914_IPC, pag. 79).")


class TestExistsInText:
    """NB: i token non sono mai incollati al punto finale della frase. Non e'
    cosmesi: `extract_candidates` include il punto nel token (comportamento
    PRE-ESISTENTE, invariato da questa modifica), quindi 'X.' e 'X' sono token
    diversi. La risposta reale misurata su C005 ha i codici seguiti da spazio."""

    def test_verdetto_distinguibile(self):
        """Non e' una promozione a EXISTS: il report deve poterlo contare."""
        keys = {_literal_key("SI-912-012"): (DOC, 79)}
        v = verify_code("SI-912-012", ["961267"], cat(), keys)
        assert v.status == "EXISTS_IN_TEXT"

    def test_c005_reale_passa(self):
        """Il caso golden C005, con la risposta REALE dell'LLM (misurata) e il
        pre-trattamento reale del nodo guardrail (strip_citations)."""
        r = check_answer(strip_citations(C005_ANSWER),
                         [evd("961267", desc=C005_DESC)], cat(),
                         citations=extract_citations(C005_ANSWER))
        assert r.gate == "PASS", [(v.raw, v.status) for v in r.verdicts]
        assert [v.status for v in r.verdicts] == [
            "EXISTS", "EXISTS_IN_TEXT", "EXISTS_IN_TEXT"]

    def test_casi_reali_misurati_passano(self):
        """I token code-shaped delle descrizioni reali (11.5% delle righe)."""
        for tok, desc in [
            ("Max.80mm", "PRESSURE GAUGE Max.80mm housing"),
            ("12V-70A", "ALTERNATOR 12V-70A"),
            ("DN19", "HOSE CLAMP DN19 stainless"),
            ("BOILER95", "SPARE KIT for BOILER95 series"),
            ("4D/4E", "GASKET SET 4D/4E series"),
        ]:
            r = check_answer(f"Si tratta di {tok} come da tabella",
                             [evd("961267", desc=desc)], cat(), citations=CITE_79)
            assert r.gate == "PASS", (tok, [(v.raw, v.status) for v in r.verdicts])

    def test_near_miss_un_carattere_bloccato(self):
        """DN19 nella descrizione, la risposta dice DN18 -> BLOCK."""
        r = check_answer("Il raccordo e DN18 come da tabella",
                         [evd("961267", desc="HOSE CLAMP DN19 stainless")],
                         cat(), citations=CITE_79)
        assert r.gate == "BLOCK", [(v.raw, v.status) for v in r.verdicts]

    def test_allucinazione_forma_dimensionale_bloccata(self):
        """DN99 non esiste da nessuna parte -> BLOCK (nessun filtro per forma:
        un token dimensionale ALLUCINATO deve restare controllato)."""
        r = check_answer("Il raccordo e DN99 come da tabella",
                         [evd("961267", desc="HOSE CLAMP DN19 stainless")],
                         cat(), citations=CITE_79)
        assert r.gate == "BLOCK", [(v.raw, v.status) for v in r.verdicts]

    def test_prefisso_non_passa_per_sottostringa(self):
        """Lezione ac8da1a: 'Max.80m' e' SOTTOSTRINGA di 'Max.80mm' ma e' un
        token DIVERSO. Confronto fra insiemi, mai `in` su stringa."""
        r = check_answer("La misura e Max.80m nominale",
                         [evd("961267", desc="PRESSURE GAUGE Max.80mm housing")],
                         cat(), citations=CITE_79)
        assert r.gate == "BLOCK", [(v.raw, v.status) for v in r.verdicts]

    def test_suffisso_non_passa_per_sottostringa(self):
        """Simmetrico: 'I-912-012' e' suffisso di 'SI-912-012' -> BLOCK."""
        r = check_answer("Il codice e I-912-012 per il supporto",
                         [evd("961267", desc=C005_DESC)], cat(), citations=CITE_79)
        assert r.gate == "BLOCK", [(v.raw, v.status) for v in r.verdicts]

    def test_riga_non_citata_verifica(self):
        """Stesso token, ma la riga che lo contiene NON e' fra le citazioni:
        e' il caso pericoloso (rimando pescato da un'altra riga) -> VERIFY."""
        r = check_answer("Il codice e SI-912-012 per il supporto",
                         [evd("961267", desc=C005_DESC, page=79)],
                         cat(), citations=[(DOC, 12)])
        assert r.gate == "VERIFY", [(v.raw, v.status) for v in r.verdicts]

    def test_documento_diverso_verifica(self):
        r = check_answer("Il codice e SI-912-012 per il supporto",
                         [evd("961267", desc=C005_DESC, page=79)],
                         cat(), citations=[("altro_doc", 79)])
        assert r.gate == "VERIFY", [(v.raw, v.status) for v in r.verdicts]

    def test_senza_citazioni_verifica_mai_pass(self):
        """Default conservativo: senza citazioni note non si puo' confermare."""
        r = check_answer("Il codice e SI-912-012 per il supporto",
                         [evd("961267", desc=C005_DESC)], cat())
        assert r.gate == "VERIFY", [(v.raw, v.status) for v in r.verdicts]

    def test_riga_senza_pagina_non_conferma(self):
        """Evidence senza (doc, page) non puo' offrire un percorso di verifica."""
        r = check_answer("Il codice e SI-912-012 per il supporto",
                         [{"code": "961267", "desc": C005_DESC, "doc": DOC,
                           "page": None, "status": "clean", "warnings": []}],
                         cat(), citations=CITE_79)
        assert r.gate == "BLOCK", [(v.raw, v.status) for v in r.verdicts]

    def test_token_nel_campo_text(self):
        """Anche il chunk narrativo ('text') e' parte della riga recuperata."""
        r = check_answer("Il codice e SI-912-012 per il supporto",
                         [evd(None, text=C005_DESC)], cat(), citations=CITE_79)
        assert r.gate == "PASS", [(v.raw, v.status) for v in r.verdicts]

    def test_evidenza_degradata_resta_verifica(self):
        """Il gancio OCR/degraded vince comunque su EXISTS_IN_TEXT."""
        r = check_answer("Il codice e SI-912-012 per il supporto",
                         [evd("961267", desc=C005_DESC, status="degraded")],
                         cat(), citations=CITE_79)
        assert r.gate == "VERIFY", [(v.raw, v.status) for v in r.verdicts]

    def test_regressione_inventato_resta_block(self):
        r = check_answer("Monta il ZZZ99999 nuovo", [evd("961267", desc=C005_DESC)],
                         cat(), citations=CITE_79)
        assert r.gate == "BLOCK", [(v.raw, v.status) for v in r.verdicts]

    def test_regressione_catalogo_resta_miss_retrieval(self):
        r = check_answer(f"Monta il {REAL_V0} nuovo", [evd("961267", desc=C005_DESC)],
                         cat(REAL_V0), citations=CITE_79)
        assert r.gate == "VERIFY"
        assert [v.status for v in r.verdicts] == ["MISS_RETRIEVAL"]

    def test_default_preserva_i_chiamanti_esistenti(self):
        """verify_code senza il nuovo parametro = comportamento invariato."""
        assert verify_code("SI-912-012", ["961267"], cat()).status == "ABSENT"

    def test_citazione_malformata_non_esplode(self):
        """Fail-closed: una citazione malformata degrada a VERIFY, non solleva."""
        r = check_answer("Il codice e SI-912-012 per il supporto",
                         [evd("961267", desc=C005_DESC)], cat(),
                         citations=[None, ("solo_doc",), (DOC, "pag79")])
        assert r.gate == "VERIFY", [(v.raw, v.status) for v in r.verdicts]
