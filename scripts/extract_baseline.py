# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pymupdf>=1.24",
#     "pymupdf4llm>=0.0.17",
#     "pandas>=2.2",
#     "openpyxl>=3.1",
# ]
# ///
"""P1.1 — Baseline di estrazione (zero-GPU) su tutto il corpus.

Termine di paragone veloce PRIMA di Marker (P1.3). Legge il text layer nativo:
- PDF  -> PyMuPDF4LLM: Markdown per pagina (page_chunks) in output/spike/baseline/<file>/
- XLSX -> pandas/openpyxl: un CSV per foglio (con puntatore all'origine)
Per ogni file scrive anche un report diagnostico _diagnostic.json con lo schema
condiviso dai punti successivi (P1.4/P1.6): tabelle rilevate, celle totali, celle
anomale (vuote / token corrotti), pagine senza testo, celle unite (XLSX).

NON e' un motore OCR e NON valuta l'accuratezza (quello e' P1.6). Serve solo a
capire dove la baseline gia' regge e dove Marker/Docling dovranno recuperare.

Uso (dipendenze risolte al volo da uv via PEP 723):
    uv run scripts/extract_baseline.py

Variabili d'ambiente:
    CORPUS_DIR (default ./corpus), OUTPUT_DIR (default ./output/spike/baseline).
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

import fitz  # PyMuPDF
import pandas as pd
import pymupdf4llm
from openpyxl import load_workbook

CORPUS_DIR = Path(os.environ.get("CORPUS_DIR", "./corpus"))
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", "./output/spike/baseline"))

# Testo utile minimo perche' una pagina PDF non sia considerata "senza testo".
NULL_TEXT_THRESHOLD = 15
# Caratteri "sospetti di corruzione": replacement char + control chars non stampabili.
CORRUPT_RE = re.compile(r"[�\x00-\x08\x0b\x0c\x0e-\x1f]")
# Marker che PyMuPDF4LLM inserisce quando il testo sta dentro una regione-immagine.
PICTURE_MARK = "<!-- Start of picture text -->"


def count_corrupt(text: str) -> int:
    return len(CORRUPT_RE.findall(text or ""))


def cell_is_empty(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def parse_markdown_tables(md: str) -> dict:
    """Conta tabelle/celle sul Markdown REALMENTE prodotto (strategia testuale).

    Su tabelle senza griglia (cataloghi ricambi) find_tables() fallisce, ma il
    Markdown testuale le rende: qui misuriamo quello. Segnala anche le celle
    multi-valore (contengono <br>): sono il sintomo di righe-multiple compresse
    in una cella / celle unite — un modo di fallimento chiave del gate.
    """
    tables = cells = empty = multivalue = 0
    in_table = False
    for line in md.splitlines():
        s = line.strip()
        is_row = s.startswith("|") and s.endswith("|") and len(s) > 1
        if not is_row:
            in_table = False
            continue
        if not in_table:
            tables += 1
            in_table = True
        # separatore markdown |---|---| non conta come dati
        cols = [c.strip() for c in s.strip("|").split("|")]
        if all(set(c) <= {"-", ":"} and c for c in cols):
            continue
        for c in cols:
            cells += 1
            if c == "":
                empty += 1
            if "<br>" in c:
                multivalue += 1
    return {"tables": tables, "cells": cells, "empty_cells": empty,
            "multivalue_cells": multivalue}


# ---------------------------------------------------------------- PDF ----------


def extract_pdf(path: Path, out_dir: Path) -> dict:
    """PyMuPDF4LLM -> Markdown per pagina; diagnostico misurato sul Markdown.

    Il diagnostico tabelle usa il Markdown realmente prodotto (strategia
    testuale), NON find_tables(): su cataloghi ricambi senza griglia find_tables
    riporta ~0 celle mentre il Markdown le rende. Misuriamo l'output reale.
    """
    doc = fitz.open(str(path))
    if doc.needs_pass:  # 3 PDF del corpus erano AES-encrypted (user pw vuota)
        doc.authenticate("")
    n_pages = doc.page_count
    doc.close()

    md_chunks = pymupdf4llm.to_markdown(str(path), page_chunks=True)
    md_dir = out_dir / "markdown"
    md_dir.mkdir(parents=True, exist_ok=True)

    pages_diag: list[dict] = []
    total_tables = total_cells = empty_cells = multivalue_cells = 0
    corrupt_tokens = null_text_pages = picture_text_pages = 0

    for i, chunk in enumerate(md_chunks, start=1):
        md_text = chunk["text"] if isinstance(chunk, dict) else str(chunk)
        (md_dir / f"{path.stem}_p{i:03d}.md").write_text(md_text, encoding="utf-8")

        chars = len(md_text.strip())
        if chars < NULL_TEXT_THRESHOLD:
            null_text_pages += 1
        has_picture = PICTURE_MARK in md_text
        if has_picture:
            picture_text_pages += 1
        corrupt_tokens += count_corrupt(md_text)

        t = parse_markdown_tables(md_text)
        total_tables += t["tables"]
        total_cells += t["cells"]
        empty_cells += t["empty_cells"]
        multivalue_cells += t["multivalue_cells"]
        pages_diag.append({"page": i, "chars": chars, "picture_text": has_picture, **t})

    return {
        "file": path.name,
        "kind": "pdf",
        "engine": "pymupdf4llm",
        "n_pages": n_pages,
        "tables_detected": total_tables,
        "total_cells": total_cells,
        "empty_cells": empty_cells,
        "multivalue_cells": multivalue_cells,
        "corrupt_tokens": corrupt_tokens,
        "null_text_pages": null_text_pages,
        "picture_text_pages": picture_text_pages,
        "pages": pages_diag,
    }


# --------------------------------------------------------------- XLSX ----------


def extract_xlsx(path: Path, out_dir: Path) -> dict:
    """pandas -> un CSV per foglio; openpyxl -> diagnostico (celle unite reali)."""
    csv_dir = out_dir / "csv"
    csv_dir.mkdir(parents=True, exist_ok=True)

    sheets = pd.read_excel(path, sheet_name=None, header=None, dtype=object)
    wb = load_workbook(filename=str(path), read_only=False, data_only=True)

    sheets_diag: list[dict] = []
    total_cells = empty_cells = merged_ranges = corrupt_tokens = 0

    for name, df in sheets.items():
        safe = re.sub(r"[^\w\-]+", "_", name).strip("_") or "sheet"
        df.to_csv(csv_dir / f"{path.stem}__{safe}.csv", index=False, header=False)

        n_cells = int(df.size)
        n_empty = int(df.isna().sum().sum())
        n_merged = len(wb[name].merged_cells.ranges) if name in wb.sheetnames else 0
        n_corrupt = sum(
            count_corrupt(v) for v in df.to_numpy().ravel().tolist()
            if isinstance(v, str)
        )
        total_cells += n_cells
        empty_cells += n_empty
        merged_ranges += n_merged
        corrupt_tokens += n_corrupt
        sheets_diag.append(
            {
                "sheet": name,
                "rows": int(df.shape[0]),
                "cols": int(df.shape[1]),
                "cells": n_cells,
                "empty_cells": n_empty,
                "merged_ranges": n_merged,
            }
        )

    wb.close()
    return {
        "file": path.name,
        "kind": "xlsx",
        "engine": "pandas+openpyxl",
        "n_sheets": len(sheets_diag),
        "tables_detected": len(sheets_diag),  # 1 tabella-logica per foglio
        "total_cells": total_cells,
        "empty_cells": empty_cells,
        "merged_ranges": merged_ranges,
        "corrupt_tokens": corrupt_tokens,
        "sheets": sheets_diag,
    }


# --------------------------------------------------------------- main ----------


def process(path: Path) -> dict:
    out_dir = OUTPUT_DIR / path.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower()
    if ext == ".pdf":
        diag = extract_pdf(path, out_dir)
    elif ext == ".xlsx":
        diag = extract_xlsx(path, out_dir)
    else:
        diag = {"file": path.name, "kind": "skip", "note": f"formato non gestito in P1.1: {ext}"}
    (out_dir / "_diagnostic.json").write_text(
        json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return diag


def main() -> int:
    if not CORPUS_DIR.is_dir():
        print(f"[baseline] ERRORE: cartella corpus assente: {CORPUS_DIR}", file=sys.stderr)
        return 1

    files = sorted(
        p for p in CORPUS_DIR.rglob("*") if p.is_file() and p.name != ".gitkeep"
    )
    if not files:
        print(f"[baseline] ERRORE: nessun file nel corpus ({CORPUS_DIR}).", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[baseline] Estrazione baseline su {len(files)} file -> {OUTPUT_DIR}\n")

    diags: list[dict] = []
    for path in files:
        print(f"[baseline] -> {path.name}")
        try:
            diags.append(process(path))
        except Exception as exc:  # noqa: BLE001 — un file rotto non ferma lo spike
            print(f"[baseline]    ERRORE su {path.name}: {type(exc).__name__}: {exc}",
                  file=sys.stderr)
            diags.append({"file": path.name, "kind": "error",
                          "error": f"{type(exc).__name__}: {exc}"})

    (OUTPUT_DIR / "_summary.json").write_text(
        json.dumps(diags, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Riepilogo a schermo
    print("\n[baseline] RIEPILOGO (baseline PyMuPDF4LLM / pandas):")
    header = f"{'FILE':45}  {'TIPO':5}  {'PAG/FGL':>7}  {'TAB':>4}  {'CELLE':>7}  {'VUOTE':>6}  {'CORR':>4}  NOTE"
    print(header)
    for d in diags:
        if d.get("kind") in {"error", "skip"}:
            print(f"{d['file'][:45]:45}  {'-':5}  {'-':>7}  {'-':>4}  {'-':>7}  {'-':>6}  {'-':>4}  {d.get('error') or d.get('note')}")
            continue
        pag = d.get("n_pages", d.get("n_sheets", "-"))
        note = []
        if d.get("picture_text_pages"):
            note.append(f"{d['picture_text_pages']} pag. picture-text")
        if d.get("multivalue_cells"):
            note.append(f"{d['multivalue_cells']} celle multi-valore(<br>)")
        if d.get("null_text_pages"):
            note.append(f"{d['null_text_pages']} pag. senza testo")
        if d.get("merged_ranges"):
            note.append(f"{d['merged_ranges']} celle unite")
        if d.get("corrupt_tokens"):
            note.append(f"{d['corrupt_tokens']} token corrotti")
        print(
            f"{d['file'][:45]:45}  {d['kind']:5}  {str(pag):>7}  "
            f"{str(d.get('tables_detected','-')):>4}  {str(d.get('total_cells','-')):>7}  "
            f"{str(d.get('empty_cells','-')):>6}  {str(d.get('corrupt_tokens',0)):>4}  {'; '.join(note)}"
        )
    print(f"\n[baseline] OK — dettaglio per file in {OUTPUT_DIR}/<file>/_diagnostic.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
