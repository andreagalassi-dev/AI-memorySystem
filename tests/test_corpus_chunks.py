"""P2.6 — Test del chunking generalizzato e del rilevatore lingua.

Debito §1: i profili arrivano da Postgres (seedato) -> marker needs_pg.
"""

from __future__ import annotations

from src.ingestion.brands import get_profile
from src.ingestion.corpus_chunks import chunk_page
from src.ingestion.lang_detect import detect_lang
from tests.conftest import needs_pg

pytestmark = needs_pg


def run(md: str, profile="default", doc="testdoc", page=1, lang="EN"):
    return chunk_page(doc, "cat", page, md, get_profile(profile), lang)


class TestTableRow:
    MD = """| Ref | Part Number | Description | Qty |
| 1 | 66M110 | Motor with brake assy | 2 |
| 2 | 66M225 | Hydraulic hose kit | 1 |
"""

    def test_colonna_codice_rilevata_default(self):
        res = run(self.MD)
        rows = [c for c in res["chunks"] if c.type == "table_row"]
        assert [c.code for c in rows] == ["66M110", "66M225"]
        assert rows[0].desc == "Motor with brake assy"
        assert rows[0].pos == "1"   # numerico a sinistra del codice = pos/ref
        assert rows[0].qty == "2"   # numerico a destra della desc = quantita'

    def test_header_non_diventa_record(self):
        res = run(self.MD)
        assert all(c.code != "Part" for c in res["chunks"])

    def test_guardia_code_not_in_source(self):
        # cella con codice che NON esiste nel resto della pagina e' impossibile
        # per costruzione (il codice viene dalla pagina); la guardia scatta se
        # la normalizzazione fabbrica una forma non presente: profilo mamoli
        # su una pagina dove il rejoin produce un codice inesistente altrove.
        md = "| V0 91568 00007 | pezzo | 1 |\n| V0 91569 00001 | altro | 2 |\n"
        res = run(md, profile="mamoli")
        # il rejoin produce V09156800007: presente nel canonico pagina -> ok
        assert [c.code for c in res["chunks"] if c.code] == \
            ["V09156800007", "V09156900001"]

    def test_cella_multi_codice_scartata(self):
        md = ("| code | desc |\n"
              "| 0USBX1 | gasket |\n"
              "| 0USBX2 0USBX3 | doppia |\n"
              "| 0USBX4 | thermostat |\n")
        res = run(md, profile="electrolux")
        assert [c.code for c in res["chunks"] if c.code] == ["0USBX1", "0USBX4"]
        assert [r["reason"] for r in res["rejects"]] == ["fused_multi_code_cell"]

    def test_tabella_senza_codici_diventa_testo(self):
        md = ("| Modello | Potenza | Peso |\n"
              "| Alfa | 3 kW | 42 kg |\n"
              "| Beta | 5 kW | 61 kg |\n")
        res = run(md)
        assert all(c.type == "text" for c in res["chunks"])
        assert any("table_without_code_column" in c.warnings for c in res["chunks"])


class TestTesto:
    def test_paragrafi_e_minimo(self):
        md = ("Intro breve.\n\n" + ("Questa e' una frase del manuale. " * 10)
              + "\n\nnota\n")
        res = run(md)
        texts = [c for c in res["chunks"] if c.type == "text"]
        assert len(texts) == 1  # 'Intro breve.' e 'nota' sotto soglia minima
        assert "frase del manuale" in texts[0].text

    def test_codice_nel_testo_resta_intero(self):
        md = "Sostituire il filtro 0USBX5 ogni sei mesi. " * 20
        res = run(md, profile="electrolux")
        for c in res["chunks"]:
            assert "0USBX5" in c.text  # mai spezzato dallo split


class TestLangDetect:
    def test_lingue(self):
        assert detect_lang("il manuale della macchina che non deve essere "
                           "usato per le operazioni non previste dal costruttore "
                           "come anche questo caso") == "IT"
        assert detect_lang("the manual of the machine that must not be used "
                           "for operations which are not described") == "EN"
        assert detect_lang("die Maschine darf nicht ohne die Anleitung und "
                           "nicht von Kindern oder durch unbefugte verwendet "
                           "werden") == "DE"

    def test_testo_scarso_undetermined(self):
        assert detect_lang("V09156800007 14R 20T") == "und"
