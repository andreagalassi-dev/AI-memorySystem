# /// script
# requires-python = ">=3.11"
# dependencies = ["docling", "pymupdf"]
# ///
"""Fase B — Estrazione Docling di UN SOLO PDF (ponte per l'auto-ingestion).

`scripts/extract_corpus.py` estrae a scala di corpus, guidato da un file di route
pre-calcolato e con percorsi relativi a `corpus/`. Per l'ingestion per-documento
(B2/B4) serve invece estrarre un file arbitrario, indicato per percorso assoluto.

Questo script e' il **ponte** verso l'env Docling isolato (PEP 723, come lo spike
P1): comunica col progetto via file. Stessa architettura decisa al gate P1
(`do_ocr=False` + TableFormer ACCURATE), estrazione PAGINA PER PAGINA con
checkpoint immediato (resume su rilancio: le pagine gia' estratte si saltano).

Uso:
  uv run scripts/extract_one.py --pdf /percorso/assoluto/file.pdf --out /dir/out
                                [--limit N] [--pages 1,2,5]
Output: <out>/p<NNNN>.md (un markdown per pagina).
Exit: 0 = tutte le pagine richieste estratte (o gia' presenti); 1 = fallimenti.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import fitz


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Estrazione Docling di un singolo PDF")
    ap.add_argument("--pdf", required=True, help="percorso (assoluto) del PDF")
    ap.add_argument("--out", required=True, help="cartella di output per i p<NNNN>.md")
    ap.add_argument("--limit", type=int, default=0,
                    help="estrai solo le prime N pagine (0 = tutte)")
    ap.add_argument("--pages", default="",
                    help="lista pagine 1-based separate da virgola (vince su --limit)")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    pdf_path = Path(args.pdf).resolve()
    out_dir = Path(args.out).resolve()
    if not pdf_path.exists():
        print(f"[extract-one] FATALE: PDF assente {pdf_path}")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)

    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})

    src = fitz.open(pdf_path)
    n_pages = src.page_count
    if args.pages.strip():
        pages = [int(p) for p in args.pages.split(",") if p.strip()]
    else:
        pages = list(range(1, n_pages + 1))
        if args.limit:
            pages = pages[: args.limit]

    done = skipped = failed = 0
    t0 = time.time()
    for pno in pages:
        if pno < 1 or pno > n_pages:
            print(f"[extract-one] salto pagina fuori range {pno} (max {n_pages})")
            continue
        md_path = out_dir / f"p{pno:04d}.md"
        if md_path.exists():
            skipped += 1
            continue
        try:
            one = fitz.open()
            one.insert_pdf(src, from_page=pno - 1, to_page=pno - 1)
            tmp = out_dir / "_tmp_page.pdf"
            one.save(tmp)
            one.close()
            res = converter.convert(str(tmp))
            md_path.write_text(res.document.export_to_markdown(), encoding="utf-8")
            tmp.unlink(missing_ok=True)
            done += 1
        except Exception as exc:  # mai silenzio: fallimento per-pagina esplicito
            print(f"[extract-one] FAIL p{pno}: {type(exc).__name__}: {exc}")
            failed += 1
    src.close()
    print(f"[extract-one] {pdf_path.name}: ok={done} gia'_presenti={skipped} "
          f"falliti={failed} in {time.time() - t0:.1f}s -> {out_dir}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
