"""P2.4 — Test del router deterministico (regole esplicite, quasi-giusti, falsi amici)."""

from __future__ import annotations

from src.retrieval.router import classify_query


def route(q):
    r = classify_query(q)
    return r.query_type, r.code_token


class TestTierAFamiglia:
    def test_codice_lungo_solo(self):
        assert route("V09156800007") == ("code", "V09156800007")

    def test_codice_lungo_minuscolo(self):
        assert route("v09156800007") == ("code", "V09156800007")

    def test_codice_lungo_con_marker(self):
        assert route("codice V09199300001") == ("code", "V09199300001")

    def test_codice_in_frase_e_mixed(self):
        t, c = route("che prezzo ha il V09199300001?")
        assert (t, c) == ("mixed", "V09199300001")

    def test_quasi_giusti_non_matchano_tier_a(self):
        # 9 e 12 alfanumerici dopo V0: fuori famiglia. In frase -> natural.
        t, _ = route("trova V0915680000070 per favore")
        assert t == "natural"
        # Da solo, un quasi-giusto passa dalla regola token-singolo (tier B):
        # e' VOLUTO — va al canale sparse dove BM25 e' tutto-o-niente, quindi
        # zero hit / score nullo -> percorso "non lo so", mai un match sbagliato.
        t, c = route("V091568000")
        assert (t, c) == ("code", "V091568000")


class TestTierBMarker:
    def test_marker_it(self):
        assert route("codice 11282") == ("code", "11282")

    def test_marker_en(self):
        assert route("part number 0USBX4") == ("code", "0USBX4")

    def test_marker_es(self):
        assert route("referencia 0USBX1") == ("code", "0USBX1")

    def test_token_singolo_con_cifre(self):
        assert route("11282") == ("code", "11282")
        assert route("0USBX4") == ("code", "0USBX4")

    def test_marker_senza_token_codey_e_natural(self):
        # 'code' come parola finale senza candidato: nessun codice
        assert route("thermostat replacement code")[0] == "natural"

    def test_mixed_marker_in_frase(self):
        t, c = route("mi dai il codice 20001E della coppia maniglie?")
        assert (t, c) == ("mixed", "20001E")


class TestFalsiAmici:
    def test_vite_m5_e_natural(self):
        # M5 contiene cifre ma non e' da solo ne' dietro marker
        assert route("vite M5")[0] == "natural"

    def test_misura_filettatura_e_natural(self):
        assert route("viti M6*16 a brugola")[0] == "natural"

    def test_prezzo_con_virgola_mai_codice(self):
        assert route("58,00")[0] == "natural"

    def test_frase_naturale(self):
        assert route("guarnizione a disco per movimenti ceramici")[0] == "natural"

    def test_query_vuota(self):
        assert route("")[0] == "natural"


class TestAuditabilita:
    def test_reason_presente(self):
        r = classify_query("codice 11282")
        assert "tier B" in r.reason and "11282" in r.reason
