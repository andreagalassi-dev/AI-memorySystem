# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "pypdf>=4.2",
#     "cryptography>=3.1",  # richiesto da pypdf per PDF AES-encrypted
#     "openpyxl>=3.1",
#     "langdetect>=1.0.9",
# ]
# ///
"""P0.6 — Catalogazione del corpus per formato e lingua.

Scansiona corpus/ e produce data/corpus_catalog.csv con, per ogni file:
formato, dimensione, (PDF) nativo vs scansionato + n. pagine, lingue rilevate.

NIENTE OCR (e' P1): sui PDF senza text layer la lingua resta 'unknown
(scansionato?)' — segnale utile in vista del gate.

Uso (dipendenze risolte al volo da uv via PEP 723):
    uv run scripts/catalog_corpus.py

Variabili d'ambiente:
    CORPUS_DIR (default ./corpus), DATA_DIR (default ./data).
"""

from __future__ import annotations

import csv
import os
import sys
from pathlib import Path

from langdetect import DetectorFactory, detect_langs
from langdetect.lang_detect_exception import LangDetectException
from openpyxl import load_workbook
from pypdf import PdfReader

DetectorFactory.seed = 0  # rende il rilevamento lingua deterministico

CORPUS_DIR = Path(os.environ.get("CORPUS_DIR", "./corpus"))
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
OUTPUT_CSV = DATA_DIR / "corpus_catalog.csv"

TARGET_LANGS = {"it", "en", "de"}
SAMPLE_CHARS = 6000            # testo campionato per il rilevamento lingua
PDF_SAMPLE_PAGES = 6          # pagine campionate dai PDF
SCANNED_CHAR_THRESHOLD = 120  # sotto questa soglia di testo: PDF "scansionato?"

FORMAT_BY_EXT = {
    ".pdf": "pdf",
    ".xlsx": "xlsx",
    ".xls": "xls",
    ".docx": "docx",
    ".doc": "doc",
    ".png": "img",
    ".jpg": "img",
    ".jpeg": "img",
    ".tif": "img",
    ".tiff": "img",
    ".dwg": "cad",
    ".dxf": "cad",
}


def detect_languages(text: str) -> str:
    """Rileva le lingue da un campione di testo. Ritorna es. 'it, en'."""
    text = (text or "").strip()
    if len(text) < 20:
        return "unknown (poco testo)"
    try:
        langs = detect_langs(text[:SAMPLE_CHARS])
    except LangDetectException:
        return "unknown"
    picked = [lang.lang for lang in langs if lang.prob >= 0.15]
    if not picked and langs:
        picked = [langs[0].lang]
    # metti in evidenza le lingue target del progetto, poi le altre
    ordered = [c for c in picked if c in TARGET_LANGS] + [
        c for c in picked if c not in TARGET_LANGS
    ]
    return ", ".join(ordered) if ordered else "unknown"


def catalog_pdf(path: Path) -> dict[str, str]:
    reader = PdfReader(str(path))
    n_pages = len(reader.pages)
    chunks: list[str] = []
    for page in reader.pages[:PDF_SAMPLE_PAGES]:
        try:
            chunks.append(page.extract_text() or "")
        except Exception:  # noqa: BLE001 — pagina illeggibile: la saltiamo
            continue
    sample = "\n".join(chunks)
    if len(sample.strip()) < SCANNED_CHAR_THRESHOLD:
        pdf_type = "scansionato?"
        langs = "unknown (scansionato?)"
    else:
        pdf_type = "nativo"
        langs = detect_languages(sample)
    return {"pdf_type": pdf_type, "n_pagine": str(n_pages), "lingue": langs, "note": ""}


def catalog_xlsx(path: Path) -> dict[str, str]:
    wb = load_workbook(filename=str(path), read_only=True, data_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            for cell in row:
                if isinstance(cell, str) and cell.strip():
                    parts.append(cell.strip())
            if sum(len(p) for p in parts) > SAMPLE_CHARS:
                break
        if sum(len(p) for p in parts) > SAMPLE_CHARS:
            break
    wb.close()
    return {
        "pdf_type": "n/a",
        "n_pagine": "n/a",
        "lingue": detect_languages(" ".join(parts)),
        "note": "",
    }


def catalog_file(path: Path) -> dict[str, str]:
    ext = path.suffix.lower()
    fmt = FORMAT_BY_EXT.get(ext, "other")
    row: dict[str, str] = {
        "file": path.name,
        "formato": fmt,
        "size_mb": f"{path.stat().st_size / 1e6:.2f}",
        "pdf_type": "n/a",
        "n_pagine": "n/a",
        "lingue": "n/a",
        "note": "",
    }
    try:
        if fmt == "pdf":
            row.update(catalog_pdf(path))
        elif fmt == "xlsx":
            row.update(catalog_xlsx(path))
        else:
            row["note"] = "formato non analizzato in P0 (solo censito)"
    except Exception as exc:  # noqa: BLE001 — un file rotto non deve fermare il catalogo
        row["note"] = f"errore lettura: {type(exc).__name__}: {exc}"
    return row


def main() -> int:
    if not CORPUS_DIR.is_dir():
        print(f"[catalog] ERRORE: cartella corpus assente: {CORPUS_DIR}", file=sys.stderr)
        return 1

    files = sorted(
        p for p in CORPUS_DIR.rglob("*") if p.is_file() and p.name != ".gitkeep"
    )
    if not files:
        print(f"[catalog] ERRORE: nessun file nel corpus ({CORPUS_DIR}).", file=sys.stderr)
        return 1

    print(f"[catalog] Analizzo {len(files)} file in {CORPUS_DIR} ...")
    rows = [catalog_file(p) for p in files]

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["file", "formato", "size_mb", "pdf_type", "n_pagine", "lingue", "note"]
    with OUTPUT_CSV.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    # riepilogo a schermo
    print(f"[catalog] OK — scritto {OUTPUT_CSV} ({len(rows)} righe)\n")
    w = max(len(r["file"]) for r in rows)
    print(f"{'FILE'.ljust(w)}  {'FORMATO':7}  {'PDF':13}  {'PAG':>4}  LINGUE")
    for r in rows:
        print(
            f"{r['file'].ljust(w)}  {r['formato']:7}  {r['pdf_type']:13}  "
            f"{r['n_pagine']:>4}  {r['lingue']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
