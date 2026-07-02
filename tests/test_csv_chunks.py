"""P2.4 — Test dell'adapter CSV (fixture sintetiche + integrazione vs GT)."""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

import pytest

from src.ingestion.csv_chunks import CSV_TABLES, chunk_csv_table, csv_cleanup

ROOT = Path(__file__).resolve().parent.parent

CFG = {
    "doc": "Test.xlsx", "sheet": "S1", "lang": "EN",
    "columns": {"pos": "NO", "code": "PNC", "desc": "DESCRIPTION", "qty": "QTY"},
    "required": ["NO", "DESCRIPTION", "PNC"],
}


def run(text: str) -> dict:
    return chunk_csv_table("TEST", text, CFG)


class TestCsvCleanup:
    def test_asterisco_preservato(self):
        # il contratto GT ha catturato 'M6*16'->'M616' con base_cleanup: mai piu'
        assert csv_cleanup("M6*16 hex socket screws") == "M6*16 hex socket screws"

    def test_spazi_e_nfkc(self):
        assert csv_cleanup("  Control  Panel ") == "Control Panel"


class TestChunkCsvTable:
    def test_banner_scartato_e_dati_estratti(self):
        text = ("banner iniziale,,,\n"
                ",,,\n"
                "NO,DESCRIPTION,PNC,QTY\n"
                "1,Door Assembly,0USBX0,1\n"
                "2,Sealing Gasket,0USBX1,2\n")
        res = run(text)
        assert res["report"]["banner_rows"] == 2
        assert len(res["chunks"]) == 2 and not res["rejects"]
        c = res["chunks"][0]
        assert (c.pos, c.code, c.desc, c.qty) == ("1", "0USBX0", "Door Assembly", "1")
        assert c.sheet == "S1" and c.page == 0 and "foglio S1" in c.text

    def test_riga_senza_codice_ma_con_desc_e_chunk(self):
        text = "NO,DESCRIPTION,PNC,QTY\n1,Power cord,,1\n"
        res = run(text)
        assert len(res["chunks"]) == 1
        assert res["chunks"][0].code is None
        assert res["chunks"][0].status == "ok"

    def test_pos_vuoto_ma_codice_presente(self):
        text = "NO,DESCRIPTION,PNC,QTY\n,Castor Without Brake,0USDSC,2\n"
        res = run(text)
        assert len(res["chunks"]) == 1 and res["chunks"][0].pos is None

    def test_riga_senza_identita_scartata(self):
        text = "NO,DESCRIPTION,PNC,QTY\n5,,,3\n"
        res = run(text)
        assert not res["chunks"]
        assert [r.reason for r in res["rejects"]] == ["no_identity"]

    def test_codice_malformato_scartato(self):
        text = "NO,DESCRIPTION,PNC,QTY\n1,Filtro,0US BX5,1\n"
        res = run(text)
        assert not res["chunks"]
        assert [r.reason for r in res["rejects"]] == ["malformed_code"]

    def test_qty_non_numerica_degradata(self):
        text = "NO,DESCRIPTION,PNC,QTY\n1,Basket,0USBX9,molte\n"
        res = run(text)
        c = res["chunks"][0]
        assert c.qty is None and c.status == "degraded" and "qty_unparseable" in c.warnings

    def test_header_mancante_esplode(self):
        with pytest.raises(ValueError):
            run("colonne,sbagliate\n1,2\n")


needs_data = pytest.mark.skipif(
    not (ROOT / "output" / "spike" / "baseline").exists(),
    reason="output/ e' gitignored: rigenerare con gli script P1",
)


@needs_data
class TestIntegrazioneCsvVsGT:
    @pytest.mark.parametrize("tid", ["DELITOOLS_EN", "ELECTROLUX_EN", "ELECTROLUX_ES"])
    def test_corrispondenza_1_a_1_col_gt(self, tid):
        cfg = CSV_TABLES[tid]
        res = chunk_csv_table(tid, (ROOT / cfg["path"]).read_text(encoding="utf-8"), cfg)
        gt = list(csv.DictReader(
            (ROOT / "data" / "ground_truth" / tid / "expected.csv").open(encoding="utf-8")))
        cgt = Counter((g["pos"], g["codice"], g["descrizione"], g["quantita"]) for g in gt)
        cch = Counter((c.pos or "", c.code or "", c.desc or "", c.qty or "")
                      for c in res["chunks"])
        assert cch == cgt
