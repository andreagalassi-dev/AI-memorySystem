# /// script
# requires-python = ">=3.11"
# dependencies = ["docling"]
# ///
"""P1.5 — Docling (do_ocr=False, TableFormer ACCURATE) sui codici Mamoli.

Confronto mirato: Docling usa il TEXT-LAYER NATIVO (do_ocr=False, come la baseline)
ma con struttura-tabella TableFormer ACCURATE. Ipotesi: codici corretti (nativi)
+ struttura migliore di PyMuPDF4LLM. Gira solo sulle pagine Mamoli del campione.

Output: output/spike/docling/raw/<table_id>/<table_id>.md
Uso (env pesante isolato):
    uv run --python 3.11 scripts/extract_docling.py
Env: MARKER_PAGES (./output/spike/marker/_pages), DOCLING_DIR (./output/spike/docling)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES = Path(os.environ.get("MARKER_PAGES", ROOT / "output" / "spike" / "marker" / "_pages"))
OUT = Path(os.environ.get("DOCLING_DIR", ROOT / "output" / "spike" / "docling")) / "raw"

TABLES = ["MAMOLI_P231_IT", "MAMOLI_P060_IT"]


def main() -> int:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False                      # <-- usa il testo nativo, niente re-OCR
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )

    OUT.mkdir(parents=True, exist_ok=True)
    for tid in TABLES:
        pdf = PAGES / f"{tid}.pdf"
        if not pdf.exists():
            print(f"[docling] salto {tid}: {pdf} assente", file=sys.stderr)
            continue
        print(f"[docling] converto {tid} ...")
        res = converter.convert(str(pdf))
        md = res.document.export_to_markdown()
        d = OUT / tid
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{tid}.md").write_text(md, encoding="utf-8")
        print(f"[docling]   -> {d / (tid + '.md')} ({len(md)} char)")
    print("[docling] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
