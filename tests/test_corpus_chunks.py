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

    def test_rilevamento_assembly_e_parent_id(self):
        md = ("| V0 91568 00000 | KIT RICAMBIO | 1 |\n"
              "| V0 91569 00001 | pezzo 1 | 2 |\n"
              "| V0 91570 00002 | pezzo 2 | 3 |\n"
              "| V0 92000 00000 | KIT SECONDO | 1 |\n"
              "| V0 92001 00001 | pezzo 3 | 1 |\n")
        prof = get_profile("mamoli").copy()
        import re
        prof["_assembly"] = re.compile(r"KIT")
        res = chunk_page("doc", "cat", 1, md, prof, "IT")
        chunks = res["chunks"]
        assert chunks[0].is_assembly is True
        assert chunks[0].parent_id is None
        assert chunks[1].is_assembly is False
        assert chunks[1].parent_id == "V09156800000"
        assert chunks[2].parent_id == "V09156800000"
        assert chunks[3].is_assembly is True
        assert chunks[3].parent_id is None
        assert chunks[4].parent_id == "V09200000000"


class TestRepairPass:
    """Debito §4 follow-up: il chunker di produzione indicizza i riparati.

    Prima della fix una riga col codice rotto dall'OCR spariva in silenzio
    (nessun chunk, nessun reject: normalize_cell non trova codici). Ora, sui
    profili con fuzzy_repair a DB, il pass di riparazione la recupera come
    chunk 'repaired' con warning fuzzy_code (cap MEDIA), esente dichiarato
    dalla guardia code_not_in_source.
    """

    MD_PREFIX_LOST = ("| V0 91568 00007 | pezzo | 1 |\n"
                      "| V0 91569 00001 | altro | 2 |\n"
                      "| 91570 00002 | rotto | 3 |\n")

    def test_prefix_restore_indicizzato_con_cap(self):
        res = run(self.MD_PREFIX_LOST, profile="mamoli")
        rep = [c for c in res["chunks"] if c.status == "repaired"]
        assert [c.code for c in rep] == ["V09157000002"]
        assert rep[0].warnings == ["fuzzy_code"]
        assert (rep[0].desc, rep[0].qty, rep[0].price) == (None, None, None)
        # i safe della stessa tabella restano intatti e non cappati
        safe = [c.code for c in res["chunks"] if c.status != "repaired" and c.code]
        assert safe == ["V09156800007", "V09156900001"]
        # nessun reject: la riga non era visibile nemmeno come scarto
        assert res["rejects"] == []

    def test_row_rejoin_codice_spezzato_dal_prezzo(self):
        md = ("| V0 91568 00007 | pezzo | 1 |\n"
              "| V0 91569 00001 | altro | 2 |\n"
              "| V0 92750 68,00 | 00001 | maniglia |\n")
        res = run(md, profile="mamoli")
        rep = [c for c in res["chunks"] if c.status == "repaired"]
        assert [c.code for c in rep] == ["V09275000001"]

    def test_collisione_con_safe_mai_emessa(self):
        # il run orfano ricomposto coincide con un codice gia' visibile:
        # nessuna riparazione (era gia' estraibile, non e' un recupero)
        md = ("| V0 91568 00007 | pezzo | 1 |\n"
              "| V0 91569 00001 | altro | 2 |\n"
              "| 91568 00007 | eco della prima | 1 |\n")
        res = run(md, profile="mamoli")
        assert not [c for c in res["chunks"] if c.status == "repaired"]

    def test_gate_per_profilo_off_nessuna_riparazione(self):
        res = run(self.MD_PREFIX_LOST, profile="default")
        assert not [c for c in res["chunks"] if c.status == "repaired"]

    def test_gate_famiglia_assente_niente_fabbricazione(self):
        # layout "listino" Mamoli: codici zero-heavy NON della famiglia V0.
        # Senza codici V0 safe sulla pagina, il prefix-restore sui run
        # numerici orfani inventerebbe codici (es. V00000000000): il gate
        # anti-fabbricazione deve tenere il repair pass spento.
        md = ("| 0000000 1B 007 | MoviMento U-tS-K | 2 | 92,00 |\n"
              "| 0000000 1D 007 | MoviMento MaMoil | 2 | 74,00 |\n"
              "| 000 0000000 4F 000 0000000 | GUarnizione | 30 | 24,00 |\n")
        res = run(md, profile="mamoli")
        assert not [c for c in res["chunks"] if c.status == "repaired"]


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
