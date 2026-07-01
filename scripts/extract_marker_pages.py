# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf>=1.24"]
# ///
"""P1.3 (step 1) — Estrae le pagine-campione in PDF di 1 pagina.

Marker non deve girare su Mamoli intero (237 pagine): estraiamo solo le pagine
candidate del campione in PDF singoli, che Marker processa in fretta.
Output: output/spike/marker/_pages/<table_id>.pdf

Uso: uv run scripts/extract_marker_pages.py
Env: CORPUS_DIR (./corpus), MARKER_DIR (./output/spike/marker)
"""

from __future__ import annotations

import os
from pathlib import Path

import fitz  # PyMuPDF

CORPUS_DIR = Path(os.environ.get("CORPUS_DIR", "./corpus"))
MARKER_DIR = Path(os.environ.get("MARKER_DIR", "./output/spike/marker"))

# (table_id, file, pagina 1-based) — solo i candidati PDF (gli XLSX non passano da Marker)
PDF_PAGES = [
    ("NORDCAP_P003_DE", "NordCap_Esploso_Ricambi_DE.pdf", 3),
    ("NORDCAP_P004_DE", "NordCap_Esploso_Ricambi_DE.pdf", 4),
    ("SILCA_P003", "Silca_TriaxPro_Ricambi_Multilingua.pdf", 3),
    ("SILCA_P004", "Silca_TriaxPro_Ricambi_Multilingua.pdf", 4),
    ("MAMOLI_P060_IT", "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf", 60),
    ("MAMOLI_P231_IT", "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf", 231),
]


def main() -> int:
    out = MARKER_DIR / "_pages"
    out.mkdir(parents=True, exist_ok=True)
    for table_id, fname, page in PDF_PAGES:
        src = fitz.open(str(CORPUS_DIR / fname))
        if src.needs_pass:
            src.authenticate("")
        dst = fitz.open()
        dst.insert_pdf(src, from_page=page - 1, to_page=page - 1)
        dst.save(str(out / f"{table_id}.pdf"))
        dst.close()
        src.close()
        print(f"[pages] {table_id:16} <- {fname} p.{page} -> _pages/{table_id}.pdf")
    print(f"\n[pages] OK — {len(PDF_PAGES)} PDF-pagina in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
