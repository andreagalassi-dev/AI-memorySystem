# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pymupdf>=1.24",
#     "pandas>=2.2",
#     "openpyxl>=3.1",
# ]
# ///
"""P1.2 — Prepara il ground truth per il gate di ingestion.

Per ogni tabella candidata (vedi docs/p1-candidate-tables.md) genera in
data/ground_truth/<table_id>/:
  - page.png        (solo PDF) rendering della pagina per la validazione visiva
  - baseline.md/csv l'estrazione baseline, come bozza da correggere
  - expected.csv    TEMPLATE da compilare/correggere a mano (la verità del gate)
  - meta.json       metadati (file, pagina/foglio, lingua/e, colonne canoniche)

expected.csv e' PRE-COMPILATO in bozza dalla baseline: l'umano CORREGGE
sull'originale (page.png / file sorgente) e conferma. NON valuta nulla: la
valutazione e' P1.6 (scripts/evaluate_ingestion_p1.py).

Uso:
    uv run scripts/prepare_ground_truth.py
Env: CORPUS_DIR (./corpus), BASELINE_DIR (./output/spike/baseline), GT_DIR (./data/ground_truth)
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

import fitz  # PyMuPDF
import pandas as pd

CORPUS_DIR = Path(os.environ.get("CORPUS_DIR", "./corpus"))
BASELINE_DIR = Path(os.environ.get("BASELINE_DIR", "./output/spike/baseline"))
GT_DIR = Path(os.environ.get("GT_DIR", "./data/ground_truth"))

STD_COLS = ["pos", "codice", "descrizione", "quantita", "prezzo"]
SILCA_COLS = ["ref", "codice", "descrizione_it", "descrizione_en",
              "descrizione_de", "descrizione_fr", "descrizione_es"]

# Registry delle tabelle candidate (allineato a docs/p1-candidate-tables.md).
CANDIDATES: list[dict] = [
    {"id": "MAMOLI_P231_IT", "file": "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf",
     "kind": "pdf", "page": 231, "lang": ["it"], "cols": STD_COLS,
     "note": "IT/EN listino denso; markup ** iniettato nei codici"},
    {"id": "MAMOLI_P060_IT", "file": "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf",
     "kind": "pdf", "page": 60, "lang": ["it"], "cols": STD_COLS,
     "note": "worst-case: righe multiple compresse in una cella (<br>)"},
    {"id": "NORDCAP_P003_DE", "file": "NordCap_Esploso_Ricambi_DE.pdf",
     "kind": "pdf", "page": 3, "lang": ["de"], "cols": STD_COLS,
     "note": "DE pulita: Bild|Artikelnr.|Artikelbezeichnung"},
    {"id": "NORDCAP_P004_DE", "file": "NordCap_Esploso_Ricambi_DE.pdf",
     "kind": "pdf", "page": 4, "lang": ["de"], "cols": STD_COLS,
     "note": "DE worst-case: header spezzato, sbavatura colonne"},
    {"id": "SILCA_P003", "file": "Silca_TriaxPro_Ricambi_Multilingua.pdf",
     "kind": "pdf", "page": 3, "lang": ["it", "en", "de", "fr", "es"], "cols": SILCA_COLS,
     "note": "multilingua: Ref|Code|IT|EN|DE|FR|ES"},
    {"id": "SILCA_P004", "file": "Silca_TriaxPro_Ricambi_Multilingua.pdf",
     "kind": "pdf", "page": 4, "lang": ["it", "en", "de", "fr", "es"], "cols": SILCA_COLS,
     "note": "continuazione tabella (multi-pagina, header solo su p.3)"},
    {"id": "ELECTROLUX_EN", "file": "Electrolux_Distinta_Ricambi.xlsx",
     "kind": "xlsx", "sheet": "PARTS LIST - ENGLISH", "lang": ["en"], "cols": STD_COLS,
     "header_marker": "DESCRIPTION",
     "map": {"pos": "NO", "codice": "COMPONENT PNC", "descrizione": "DESCRIPTION",
             "quantita": "QTY"},
     "note": "riferimento EN pulito"},
    {"id": "ELECTROLUX_ES", "file": "Electrolux_Distinta_Ricambi.xlsx",
     "kind": "xlsx", "sheet": "PARTS LIST - SPANISH", "lang": ["es"], "cols": STD_COLS,
     "header_marker": "DESCRIP",
     "map": {"pos": "NO", "codice": "COMPONENTE", "descrizione": "DESCRIP",
             "quantita": "QTY"},
     "note": "riferimento ES pulito"},
    {"id": "DELITOOLS_EN", "file": "DeliTools_SpareParts_List.xlsx",
     "kind": "xlsx", "sheet": "Sheet1", "lang": ["en"], "cols": STD_COLS,
     "header_marker": "Positional",
     "map": {"pos": "Part Positional Number", "codice": "Part Number",
             "descrizione": "Description - English", "quantita": "Bom Q'ty"},
     "note": "riferimento EN (+ZH); Part Number spesso vuoto -> chiave = pos"},
]

MD_ROW = re.compile(r"^\s*\|.*\|\s*$")
SEP_ROW = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def md_table_rows(md: str) -> list[list[str]]:
    """Estrae le righe di tabella (liste di celle) dal Markdown di una pagina."""
    rows: list[list[str]] = []
    for line in md.splitlines():
        if not MD_ROW.match(line) or SEP_ROW.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        rows.append(cells)
    return rows


def write_expected_pdf(out: Path, cols: list[str], md_rows: list[list[str]]) -> None:
    """Template expected.csv pre-seed: colonne canoniche + celle grezze come aiuto."""
    with (out / "expected.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(cols + ["_source_cells (bozza da baseline — CORREGGERE)"])
        for r in md_rows:
            joined = " ┃ ".join(r)
            w.writerow([""] * len(cols) + [joined])


def prepare_pdf(c: dict, out: Path) -> dict:
    src = CORPUS_DIR / c["file"]
    doc = fitz.open(str(src))
    if doc.needs_pass:
        doc.authenticate("")
    page = doc.load_page(c["page"] - 1)  # 1-based -> 0-based
    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))  # ~144 DPI
    pix.save(str(out / "page.png"))
    doc.close()

    stem = Path(c["file"]).stem
    md_path = BASELINE_DIR / stem / "markdown" / f"{stem}_p{c['page']:03d}.md"
    md = md_path.read_text(encoding="utf-8") if md_path.exists() else ""
    (out / "baseline.md").write_text(md, encoding="utf-8")
    write_expected_pdf(out, c["cols"], md_table_rows(md))
    return {"n_baseline_rows": len(md_table_rows(md)), "page_png": True}


def prepare_xlsx(c: dict, out: Path) -> dict:
    src = CORPUS_DIR / c["file"]
    raw = pd.read_excel(src, sheet_name=c["sheet"], header=None, dtype=object)

    # trova la riga header cercando il marker, poi rileggi con quell'header
    marker = c["header_marker"].lower()
    hdr_idx = None
    for i, row in raw.iterrows():
        joined = " ".join(str(v) for v in row.tolist() if pd.notna(v)).lower()
        if marker in joined:
            hdr_idx = i
            break
    if hdr_idx is None:
        (out / "baseline.csv").write_text(raw.to_csv(index=False, header=False), encoding="utf-8")
        return {"warning": f"header_marker '{c['header_marker']}' non trovato", "header_row": None}

    df = pd.read_excel(src, sheet_name=c["sheet"], header=hdr_idx, dtype=object)
    df.columns = [str(x).strip() for x in df.columns]
    df.to_csv(out / "baseline.csv", index=False)

    # pre-compila expected.csv mappando le colonne sorgente -> canoniche
    def col(name: str):
        for real in df.columns:
            if name.lower() in real.lower():
                return real
        return None

    mapping = {canon: col(src_name) for canon, src_name in c["map"].items()}
    rows_out = []
    for _, r in df.iterrows():
        row = {k: "" for k in c["cols"]}
        for canon, real in mapping.items():
            if real is not None and pd.notna(r.get(real)):
                row[canon] = str(r[real]).strip()
        if any(v for v in row.values()):
            rows_out.append(row)

    with (out / "expected.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=c["cols"])
        w.writeheader()
        w.writerows(rows_out)
    return {"header_row": int(hdr_idx), "n_rows": len(rows_out),
            "mapping": {k: v for k, v in mapping.items()}}


def main() -> int:
    GT_DIR.mkdir(parents=True, exist_ok=True)
    index: list[dict] = []
    print(f"[gt] Preparo {len(CANDIDATES)} tabelle candidate -> {GT_DIR}\n")
    for c in CANDIDATES:
        out = GT_DIR / c["id"]
        out.mkdir(parents=True, exist_ok=True)
        try:
            extra = prepare_pdf(c, out) if c["kind"] == "pdf" else prepare_xlsx(c, out)
        except Exception as exc:  # noqa: BLE001
            extra = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"[gt]  ! {c['id']}: {extra['error']}")
        meta = {
            "table_id": c["id"], "file": c["file"], "kind": c["kind"],
            "page": c.get("page"), "sheet": c.get("sheet"),
            "lang": c["lang"], "cols": c["cols"], "note": c["note"], **extra,
        }
        (out / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False),
                                       encoding="utf-8")
        index.append(meta)
        status = extra.get("error") or f"rows={extra.get('n_rows', extra.get('n_baseline_rows','?'))}"
        print(f"[gt]  {c['id']:16} {c['kind']:5} lang={','.join(c['lang']):14} {status}")

    (GT_DIR / "_index.json").write_text(json.dumps(index, indent=2, ensure_ascii=False),
                                        encoding="utf-8")
    print(f"\n[gt] OK — {len(index)} cartelle in {GT_DIR}. "
          f"Da compilare a mano: <table_id>/expected.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
