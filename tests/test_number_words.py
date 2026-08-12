"""Normalizzatore dei NUMERI SCRITTI A PAROLE (src/retrieval/expansion.py).

Classe di guasto misurata sul ramo narrativo: l'utente detta la misura a voce
("vite testa svasata CINQUE PER TRENTA"), il documento la scrive in cifre
("VITE M5x30 UNI 7688"). Ne' BM25 ne' il cross-encoder fanno quel ponte.

I test sono ancorati a stringhe REALI del corpus indicizzato (`aike_chunks`):
  "VITE M5x30 UNI 7688 ZINC NERO"   manzoni_comac_spareparts_B p14
  "VTE M8X20 UNI 5739 Z.G"          benassi_t623_flail_mower p7
  "O-Ring 25 x 3,2mm"               NordCap_Esploso_Ricambi_DE p4
  "SCHLAUCH 4X7 SILIKON DURCHS."    kbs_gastrotechnik_1717 p14
  "MOLLA DIAM 8.5X26"               benassi_t623_flail_mower p27
  "BOLT M12×100"                    iwaki_TC-X500PH-FLA p4
Le forme attese qui non sono un'ipotesi: sono state lette nel corpus.
"""

from __future__ import annotations

import pytest

from src.retrieval.expansion import expand_number_words


def has(query: str, *forms: str) -> None:
    out = expand_number_words(query)
    missing = [f for f in forms if f not in out]
    assert not missing, f"{query!r} -> {out} (mancano {missing})"


class TestCoppieDimensionali:
    def test_it_cinque_per_trenta(self):
        # il caso N003 del golden gate: target 'VITE M5x30 UNI 7688 ZINC NERO'
        has("vite testa svasata cinque per trenta zincata scura",
            "m5x30", "5x30", "5 x 30", "m5 x 30")

    def test_it_otto_per_venti(self):
        has("bullone otto per venti zincato testa esagonale", "m8x20", "8x20")

    def test_de_auf_con_decimale_komma(self):
        # 'O-Ring 25 x 3,2mm': la forma SPAZIATA e' l'unica che il BM25 aggancia
        has("Dichtring fünfundzwanzig auf drei Komma zwei", "25 x 3,2")

    def test_de_vier_auf_sieben(self):
        has("durchsichtiger Silikonschlauch vier auf sieben", "4x7")

    def test_de_mal(self):
        has("Blech zwanzig mal dreissig", "20x30", "m20x30")

    def test_en_by(self):
        has("hex head bolt eight by twenty", "m8x20", "8x20")

    def test_en_x_letterale(self):
        has("gasket twenty five x three", "25x3", "25 x 3")

    def test_it_dodici_per_cento(self):
        # 'BOLT M12×100': il separatore × spezza, serve la forma spaziata
        has("bullone dodici per cento della pompa", "m12 x 100", "12 x 100")

    def test_cifra_piu_parola_mista(self):
        has("guarnizione 25 per tre", "25x3", "m25x3")

    def test_decimale_it_virgola(self):
        has("o-ring venticinque virgola cinque per tre", "25,5 x 3")


class TestNumeriIsolati:
    def test_it_isolato(self):
        assert "10" in expand_number_words("tubetto di rame da dieci")

    def test_it_e_mezzo(self):
        # 'MOLLA DIAM 8.5X26'
        assert "8,5" in expand_number_words("molla da otto e mezzo per la trincia")

    def test_it_decine_composte(self):
        assert "28" in expand_number_words("perno da ventotto")
        assert "42" in expand_number_words("piastra quarantadue")
        assert "31" in expand_number_words("albero trentuno")

    def test_de_decine_composte(self):
        assert "25" in expand_number_words("Dichtring fünfundzwanzig")
        assert "21" in expand_number_words("Welle einundzwanzig")
        assert "93" in expand_number_words("Blech dreiundneunzig")

    def test_en_decine_composte(self):
        assert "25" in expand_number_words("shaft twenty-five")
        assert "25" in expand_number_words("shaft twenty five")

    def test_cento(self):
        assert "100" in expand_number_words("bullone da cento")


class TestNonSpara:
    def test_articoli_indeterminativi_non_sono_numeri(self):
        # 'un/una/uno' e i loro fratelli EN/DE sono articoli molto piu' spesso
        # che numeri: emettere '1' inquinerebbe ogni query italiana.
        for q in ("mi serve un movimento ceramico di ricambio",
                  "una guarnizione per il vitone",
                  "a replacement door hinge",
                  "eine Dichtung für den Boiler"):
            assert expand_number_words(q) == [], q

    def test_solo_cifre_niente_da_normalizzare(self):
        assert expand_number_words("aeratore 24x1 di ricambio") == []
        assert expand_number_words("soffione doccia con diametro 220") == []

    def test_parole_che_contengono_un_numero_non_matchano(self):
        # match esatto sul token: nessun prefisso, nessuna sottostringa
        assert expand_number_words("centoventisette centrifuga settimana") == []

    def test_nessun_crash_su_input_degeneri(self):
        for q in ("", "   ", "per", "cento per", "per cinque", "x x x", None):
            assert isinstance(expand_number_words(q), list)


class TestContratto:
    def test_deterministico_e_senza_duplicati(self):
        q = "vite cinque per trenta e dado cinque per trenta"
        a = expand_number_words(q)
        assert a == expand_number_words(q)
        assert len(a) == len(set(a))

    def test_cappato(self):
        q = ("uno due tre quattro cinque sei sette otto nove dieci undici "
             "dodici tredici quattordici quindici sedici")
        assert len(expand_number_words(q)) <= 16

    def test_la_query_non_viene_mai_riscritta(self):
        # e' un emettitore di termini AGGIUNTIVI: input immutato, mai sostituito
        q = "vite testa svasata cinque per trenta"
        out = expand_number_words(q)
        assert q == "vite testa svasata cinque per trenta"
        assert q not in out


class TestIntegrazioneExpandQuery:
    """I termini numerici si SOMMANO a quelli del dizionario sinonimi."""

    def test_expand_query_include_i_termini_numerici(self, monkeypatch):
        from src.retrieval import expansion
        from src.retrieval.expansion import expand_query, reload_synonyms

        monkeypatch.setattr(expansion, "_load_from_db", lambda: {
            "vite": {"note": None, "terms": [["vite", "IT"], ["screw", "EN"]]}})
        reload_synonyms()
        try:
            out = expand_query("vite cinque per trenta")
            assert "screw" in out          # sinonimi: invariati
            assert "m5x30" in out          # numeri: additivi
        finally:
            reload_synonyms()

    def test_expand_query_su_query_senza_numeri_invariata(self, monkeypatch):
        from src.retrieval import expansion
        from src.retrieval.expansion import expand_query, reload_synonyms

        monkeypatch.setattr(expansion, "_load_from_db", lambda: {
            "vite": {"note": None, "terms": [["vite", "IT"], ["screw", "EN"]]}})
        reload_synonyms()
        try:
            assert expand_query("vite del carter") == ["screw"]
        finally:
            reload_synonyms()


@pytest.mark.parametrize("query,forma", [
    ("vite testa svasata cinque per trenta zincata scura", "m5x30"),
    ("bullone otto per venti zincato testa esagonale", "m8x20"),
    ("Dichtring fünfundzwanzig auf drei Komma zwei", "25 x 3,2"),
    ("durchsichtiger Silikonschlauch vier auf sieben", "4x7"),
])
def test_casi_reali_del_set_narrativo(query, forma):
    assert forma in expand_number_words(query)
