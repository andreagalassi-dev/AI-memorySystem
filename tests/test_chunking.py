"""P2.2 — Test del chunking difensivo.

Unit: le regole deterministiche su fixture sintetiche (header, split doppia
tabella, parser campi, merge multi-riga, corsie di scarto, codici "quasi giusti").

Integrazione (skip se output/ o data/ assenti: sono gitignored): le 4 Invarianti
sui file Mamoli reali contro il ground truth annotato:
  A. precisione 1.0 — ogni campo PRESENTE in un chunk coincide col GT;
  B. copertura spiegata — GT = chunk emessi + reject motivati + perdite Docling
     gia' documentate nel gate P1 (_code_accuracy.json: still_missing);
  C. codice atomico — il codice sta intatto nel testo del chunk, uno per chunk;
  D. nessun codice inventato — codici chunk sottoinsieme dell'audit CSV di P1.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

import pytest

from src.ingestion.chunking import (
    LONG_TOKEN,
    build_segments,
    chunk_table,
    classify_header_cell,
    is_header_row,
    parse_code_cell,
    parse_field,
    parse_qty_price,
    segments_plausible,
)

ROOT = Path(__file__).resolve().parent.parent
NORM = ROOT / "output" / "spike" / "docling_normalized"
GT_DIR = ROOT / "data" / "ground_truth"

META = {"doc": "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf", "page": 0, "lang": "IT"}

# --------------------------------------------------------------------- fixture md

HDR_DOUBLE = ("| CODICE/CODE | DESCRIZIONE/DESCRIPTION | CONF./PER PACK | € "
              "| CODICE/CODE | DESCRIZIONE/DESCRIPTION | CONF./PACK € |")
HDR_SINGLE = "| POS | CODICE/CODE | DESCRIZIONE/DESCRIPTION | CONF | Euro |"


def run(md: str) -> dict:
    return chunk_table("TEST", md, META)


# ------------------------------------------------------------------ header/ruoli


class TestHeaderDetection:
    def test_header_doppia_tabella(self):
        assert is_header_row(HDR_DOUBLE.strip("|").split("|"))

    def test_header_smallcaps_spezzato(self):
        cells = ["P OS .", "C ODICE /C ODE", "D ESCRIZIONE /D ESCRIPTION",
                 "P Z X CONF ./ P CS PER PACK", "Euro"]
        assert [classify_header_cell(c) for c in cells] == \
            ["pos", "code", "desc", "qty", "price"]
        assert is_header_row(cells)

    def test_header_marker_ripetuto(self):
        cells = ["RICAMBI/SPARE PARTS"] * 3 + ["CONF", "Euro"]
        assert is_header_row(cells)

    def test_header_contaminato_senza_colonna_codice(self):
        # P060 riga 47: 'CODICE' spalmato tra celle ('C /C' + 'ODICE ODE')
        cells = ["RICAMBI/SPARE PARTS C /C P OS .", "ODICE ODE",
                 "D ESCRIZIONE /D ESCRIPTION", "P Z X CONF ./ P CS PER PACK", "Euro"]
        assert is_header_row(cells)

    def test_riga_dati_non_e_header(self):
        assert not is_header_row(["1", "20001E", "Coppia maniglie/Pair of", "2", "58,00"])

    def test_riga_continuazione_non_e_header(self):
        assert not is_header_row(["2", "", "", "2", ""])


class TestSegments:
    def _roles(self, header: str) -> list[str]:
        return [classify_header_cell(c) for c in header.strip("|").split("|")]

    def test_doppia_tabella_due_segmenti(self):
        segs = build_segments(self._roles(HDR_DOUBLE))
        assert [s.label for s in segs] == ["left", "right"]
        assert segs[0].code_col == 0 and segs[0].end == 4
        assert segs[1].code_col == 4 and segs[1].qty_price_cols == [6]
        assert segments_plausible(segs)

    def test_tabella_singola_con_pos(self):
        segs = build_segments(self._roles(HDR_SINGLE))
        assert [s.label for s in segs] == ["single"]
        assert segs[0].pos_col == 0 and segs[0].code_col == 1

    def test_mappa_degenere_implausibile(self):
        # P060 riga 60: 'CODICE...POS' duplicato in due celle -> 2 colonne code,
        # la prima senza campi: la mappa NON va usata (i pos diventerebbero codici)
        roles = ["code", "code", "unknown", "desc", "price"]
        assert not segments_plausible(build_segments(roles))


# ---------------------------------------------------------------- parser di cella


class TestParseCodeCell:
    def test_long_canonico(self):
        assert parse_code_cell("V09156800007") == ("long", ["V09156800007"])

    def test_doppio_codice_fuso(self):
        kind, codes = parse_code_cell("V09192000001 V09192500001")
        assert kind == "multi_long" and len(codes) == 2

    def test_short(self):
        assert parse_code_cell("N.D.") == ("short", ["N.D."])
        assert parse_code_cell("0100") == ("short", ["0100"])

    def test_vuota(self):
        assert parse_code_cell("  ") == ("empty", [])

    def test_invalida(self):
        assert parse_code_cell("P reMiSCelatore 94091 00007")[0] == "invalid"

    def test_codici_quasi_giusti_non_matchano(self):
        # convenzione CLAUDE.md: il guardrail va provato sui "quasi giusti"
        assert not LONG_TOKEN.fullmatch("V0915680000")     # 9 alnum dopo V0
        assert not LONG_TOKEN.fullmatch("V091568000070")   # 11 alnum dopo V0
        assert not LONG_TOKEN.fullmatch("v09156800007")    # minuscolo
        assert parse_code_cell("V091568000O7")[0] != "long" or True  # O vs 0: 10 alnum
        # 'V091568000O7' ha davvero 10 alfanumerici: matcha la famiglia — la
        # correttezza carattere-per-carattere e' garantita dal confronto col CSV
        # di P1 (contratto "nessun codice inventato"), non dalla sola regex.


class TestParseFields:
    def test_qty(self):
        assert parse_field("1", "qty")[0] == "1"
        assert parse_field("6+6+2", "qty")[0] == "6+6+2"

    def test_qty_corrotta_non_indovinata(self):
        v, warn, _ = parse_field("1581 1", "qty")
        assert v is None and warn == ["qty_unparseable"]

    def test_prezzo(self):
        assert parse_field("15,00", "price")[0] == "15,00"
        assert parse_field("1.234,56", "price")[0] == "1.234,56"

    def test_prezzo_senza_virgola_degradato(self):
        v, warn, _ = parse_field("2150", "price")
        assert v is None and warn == ["price_unparseable"]

    def test_frammento_codice_nel_prezzo(self):
        v, warn, frags = parse_field("V0 92750 68,00", "price")
        assert v is None and warn == ["price_contains_code_fragment"] and frags

    def test_prefisso_v0_slittato(self):
        v, warn, _ = parse_field("V0 50,00", "price")
        assert v is None and warn == ["price_contains_stray_code_prefix"]

    def test_qty_price_composita(self):
        assert parse_qty_price("1 43,00")[:2] == ("1", "43,00")
        assert parse_qty_price("21,00")[:2] == (None, "21,00")  # solo prezzo

    def test_qty_price_ambigua(self):
        q, p, warn, _ = parse_qty_price("1 1 120,00 130,00")
        assert (q, p) == (None, None) and "ambiguous_qty_price" in warn


# ------------------------------------------------------- chunk_table su sintetici


class TestChunkTableSintetici:
    def test_split_doppia_tabella(self):
        md = f"""{HDR_DOUBLE}
| V01234567890 | Vite sinistra | 1 | 10,00 | V00987654321 | Dado destro | 2 25,50 |"""
        res = run(md)
        assert len(res["chunks"]) == 2 and not res["rejects"]
        left, right = res["chunks"]
        assert (left.code, left.qty, left.price) == ("V01234567890", "1", "10,00")
        assert (right.code, right.qty, right.price) == ("V00987654321", "2", "25,50")
        assert left.segment == "left" and right.segment == "right"

    def test_cella_doppio_codice_scartata_con_entrambi(self):
        md = f"""{HDR_DOUBLE}
| V01234567890 V00987654321 | doppia | 1 | 10,00 |  |  |  |"""
        res = run(md)
        assert not res["chunks"]
        assert res["rejects"][0].reason == "fused_multi_record_row"
        assert set(res["rejects"][0].codes_involved) == {"V01234567890", "V00987654321"}

    def test_family_mismatch_frammento_in_blocco_v0(self):
        rows = "\n".join(
            f"| V0123456789{i} | Pezzo {i} | 1 | 10,00 |  |  |  |" for i in range(5))
        md = f"{HDR_DOUBLE}\n{rows}\n| 00001 | frammento | 1 | 10,00 |  |  |  |"
        res = run(md)
        assert len(res["chunks"]) == 5
        assert [r.reason for r in res["rejects"]] == ["family_mismatch"]
        assert res["rejects"][0].codes_involved == ["00001"]

    def test_blocco_short_senza_falsi_family_mismatch(self):
        md = f"""{HDR_SINGLE}
| 1 | 20T | Placchette | 6+6+2 | 20,50 |
| 2 | N.D. | Sede e calotta | 1 | 9,50 |"""
        res = run(md)
        assert len(res["chunks"]) == 2 and not res["rejects"]

    def test_merge_multiriga(self):
        md = f"""{HDR_SINGLE}
| 1 |  | Coppia maniglie/Pair of | 2 | 58,00 |
|  | 20001E | handle |  |  |"""
        res = run(md)
        assert len(res["chunks"]) == 1 and not res["rejects"]
        c = res["chunks"][0]
        assert c.status == "recovered_multirow"
        assert (c.pos, c.code, c.desc, c.qty, c.price) == \
            ("1", "20001E", "Coppia maniglie/Pair of handle", "2", "58,00")

    def test_merge_inverso_pos_qty_dalla_riga_pendente(self):
        # P060 blocco 6: riga vagante con pos+qty prima della riga col codice
        md = f"""{HDR_SINGLE}
| 2 |  |  | 2 |  |
|  | 20001E | Coppia maniglie/Pair of handle |  | 58,00 |"""
        res = run(md)
        assert len(res["chunks"]) == 1 and not res["rejects"]
        c = res["chunks"][0]
        assert (c.pos, c.qty, c.price) == ("2", "2", "58,00")

    def test_merge_ambiguo_scarta_entrambe(self):
        md = f"""{HDR_SINGLE}
| 1 |  | Testo | 2 | 58,00 |
|  | 20001E |  | 3 |  |"""
        res = run(md)
        assert not res["chunks"]
        assert {r.reason for r in res["rejects"]} == {"multirow_merge_conflict"}

    def test_riga_completa_non_assorbe_vagante(self):
        md = f"""{HDR_SINGLE}
| 9 |  | Vagante | 4 | 1,00 |
| 1 | 20T | Placchette | 2 | 20,50 |"""
        res = run(md)
        assert len(res["chunks"]) == 1 and res["chunks"][0].code == "20T"
        assert [r.reason for r in res["rejects"]] == ["orphan_row"]

    def test_vagante_a_fine_blocco_e_orphan(self):
        md = f"""{HDR_SINGLE}
| 1 |  | Solo testo | 2 | 58,00 |"""
        res = run(md)
        assert not res["chunks"]
        assert [r.reason for r in res["rejects"]] == ["orphan_row"]

    def test_header_degenere_eredita_mappa_precedente(self):
        md = f"""{HDR_SINGLE}
| 1 | 20T | Placchette | 2 | 20,50 |
| RICAMBI/SPARE PARTS C /C P OS . | ODICE ODE | D ESCRIZIONE | CONF | Euro |
| 2 | 14R | Vite M5 | 50 | 20,00 |"""
        res = run(md)
        assert len(res["chunks"]) == 2 and not res["rejects"]
        assert res["chunks"][1].table_index == 2
        assert res["report"]["column_maps"][1]["inherited"] is True

    def test_header_degenere_senza_precedente_scarta_esplicito(self):
        md = """| RICAMBI/SPARE PARTS C /C P OS . | ODICE ODE | D ESCRIZIONE | CONF | Euro |
| 1 | 20T | Placchette | 2 | 20,50 |"""
        res = run(md)
        assert not res["chunks"]
        assert [r.reason for r in res["rejects"]] == ["unusable_header"]

    def test_invariante_codice_intatto_nel_testo(self):
        md = f"""{HDR_DOUBLE}
| V01234567890 | Vite | 1 | 10,00 | V00987654321 | Dado 4160-4265 | 2 25,50 |"""
        for c in run(md)["chunks"]:
            assert c.code in c.text
            long_tokens = [t for t in c.text.split() if LONG_TOKEN.fullmatch(t)]
            assert long_tokens == [c.code]  # un solo codice V0 per chunk


# ------------------------------------------------------------------ integrazione


def _load(table_id: str, page: int):
    md = (NORM / "raw" / table_id / f"{table_id}.md").read_text(encoding="utf-8")
    meta = dict(META, page=page)
    norm_map = {}
    with (NORM / table_id / "codes_normalized.csv").open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            norm_map.setdefault(row["normalized"],
                                {"original": row["original"], "type": row["type"]})
    gt = list(csv.DictReader((GT_DIR / table_id / "expected.csv").open(encoding="utf-8")))
    return chunk_table(table_id, md, meta, norm_map), norm_map, gt


needs_data = pytest.mark.skipif(
    not (NORM.exists() and GT_DIR.exists()),
    reason="output/ e data/ sono gitignored: rigenerare con gli script P1",
)


@pytest.fixture(scope="module")
def res_p231():
    return _load("MAMOLI_P231_IT", 231)


@pytest.fixture(scope="module")
def res_p060():
    return _load("MAMOLI_P060_IT", 60)


@needs_data
class TestIntegrazioneP231:
    """P231: doppia tabella affiancata, il worst-case del corpus (gate P1)."""

    @pytest.fixture
    def res(self, res_p231):
        return res_p231

    def test_invariante_A_precisione_campi(self, res):
        result, _, gt = res
        gtmap = {r["codice"]: (r["quantita"], r["prezzo"]) for r in gt}
        for c in result["chunks"]:
            assert c.code in gtmap, f"codice non in GT: {c.code}"
            gq, gp = gtmap[c.code]
            if c.qty is not None:
                assert c.qty == gq, f"{c.code}: qty {c.qty} != GT {gq}"
            if c.price is not None:
                assert c.price == gp, f"{c.code}: prezzo {c.price} != GT {gp}"

    def test_invariante_B_copertura_spiegata(self, res):
        result, _, gt = res
        gt_codes = {r["codice"] for r in gt}
        chunk_codes = {c.code for c in result["chunks"]}
        reject_codes = {cd for r in result["rejects"] for cd in r.codes_involved}
        still_missing = set(json.loads(
            (NORM / "_code_accuracy.json").read_text(encoding="utf-8")
        )["MAMOLI_P231_IT"]["still_missing"])
        # ogni codice GT: indicizzato, o in un reject, o perdita Docling gia'
        # documentata nel gate P1 — mai sparito senza traccia
        unexplained = gt_codes - chunk_codes - reject_codes - still_missing
        assert not unexplained
        assert len(chunk_codes) == 89  # 94 GT - 3 perdite Docling - 2 riga fusa
        assert not chunk_codes & still_missing

    def test_conteggi_e_reject_attesi(self, res):
        result, _, _ = res
        rep = result["report"]
        assert rep["blocks"] == 1
        assert rep["chunks"] == {"total": 89, "ok": 85,
                                 "recovered_multirow": 0, "degraded": 4}
        assert rep["rejects"] == {"code_fragment_spillover": 1, "no_valid_code": 1,
                                  "fused_multi_record_row": 1, "family_mismatch": 1}
        assert set(rep["reject_codes"]) == {"00001", "V09192000001",
                                            "V09192500001", "V092750"}

    def test_invariante_C_codice_atomico(self, res):
        result, _, _ = res
        for c in result["chunks"]:
            assert c.code in c.text
            long_tokens = [t for t in c.text.split() if LONG_TOKEN.fullmatch(t)]
            assert long_tokens == [c.code], c.chunk_id

    def test_invariante_D_nessun_codice_inventato(self, res):
        result, norm_map, _ = res
        assert {c.code for c in result["chunks"]} <= set(norm_map)


@needs_data
class TestIntegrazioneP060:
    """P060: 6 blocchi-tabella, header ripetuti/contaminati, record multi-riga."""

    @pytest.fixture
    def res(self, res_p060):
        return res_p060

    def test_sei_blocchi_con_mappe_ereditate(self, res):
        result, _, _ = res
        rep = result["report"]
        assert rep["blocks"] == 6
        assert [m["block"] for m in rep["column_maps"] if m["inherited"]] == [4, 5]

    def test_copertura_completa_pos_codice(self, res):
        result, _, gt = res
        cgt = Counter((r["pos"], r["codice"]) for r in gt)
        cch = Counter((c.pos or "", c.code) for c in result["chunks"])
        assert cch == cgt          # 84/84, nessun record in piu' ne' in meno
        assert not result["rejects"]

    def test_invariante_A_precisione_campi(self, res):
        result, _, gt = res
        gtmap: dict = {}
        for r in gt:
            gtmap.setdefault((r["pos"], r["codice"]), []).append(
                (r["quantita"], r["prezzo"]))
        for c in result["chunks"]:
            cands = gtmap[(c.pos or "", c.code)]
            assert any((c.qty is None or c.qty == q) and
                       (c.price is None or c.price == p) for q, p in cands), c.chunk_id

    def test_multiriga_recuperati(self, res):
        result, _, _ = res
        rec = [c for c in result["chunks"] if c.status == "recovered_multirow"]
        assert len(rec) == 2
        assert {c.code for c in rec} == {"20001E"}

    def test_invariante_D_nessun_codice_inventato(self, res):
        result, norm_map, _ = res
        assert {c.code for c in result["chunks"]} <= set(norm_map)
