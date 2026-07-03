# /// script
# requires-python = ">=3.11"
# dependencies = ["docling", "pymupdf"]
# ///
"""P2.6 — Estrazione massiva Docling delle pagine instradate a route_docling.

Env pesante ISOLATO (PEP723, come lo spike P1): comunica col progetto via file.
Converte PAGINA PER PAGINA (PDF monopagina temporaneo, converter riusato) con
do_ocr=False + TableFormer ACCURATE — l'architettura decisa al gate P1.

CHECKPOINT PER-PAGINA (raccomandazione utente: resistere a OOM-kill):
  - output/extraction/<doc_stem>/p<NNNN>.md scritto subito dopo ogni pagina;
  - riga JSONL append+flush su _progress.jsonl dopo OGNI pagina (doc, page,
    secondi, chars, ok|fail+errore);
  - rilancio = resume automatico (pagine gia' estratte saltate).

Env di controllo:
  EXTRACT_LIMIT=N    solo le prime N pagine mancanti (pilota di misura)
  EXTRACT_ROUTE=...  file di route alternativo

Uso: uv run scripts/extract_corpus.py
Exit: 0 = tutte le pagine richieste estratte (o gia' presenti); 1 = fallimenti.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
ROUTE = Path(os.environ.get("EXTRACT_ROUTE",
                            ROOT / "output" / "spike" / "page_classes"
                            / "_route_docling.txt"))
OUT = ROOT / "output" / "extraction"
CORPUS = ROOT / "corpus"
LIMIT = int(os.environ.get("EXTRACT_LIMIT", "0"))


def parse_route() -> dict[str, list[int]]:
    """'cat/doc.pdf#pN' -> {percorso_pdf: [pagine 1-based]}."""
    by_doc: dict[str, list[int]] = defaultdict(list)
    for line in ROUTE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        ref, page = line.rsplit("#p", 1)
        by_doc[ref].append(int(page))
    return {k: sorted(v) for k, v in sorted(by_doc.items())}


def main() -> int:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, TableFormerMode
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = PdfPipelineOptions()
    opts.do_ocr = False
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})

    OUT.mkdir(parents=True, exist_ok=True)
    progress = (OUT / "_progress.jsonl").open("a", encoding="utf-8")
    by_doc = parse_route()
    total = sum(len(v) for v in by_doc.values())
    done = skipped = failed = 0
    t_start = time.time()

    for ref, pages in by_doc.items():
        pdf_path = CORPUS / ref
        stem = pdf_path.stem
        doc_out = OUT / stem
        doc_out.mkdir(parents=True, exist_ok=True)
        src = fitz.open(pdf_path)
        for pno in pages:
            md_path = doc_out / f"p{pno:04d}.md"
            if md_path.exists():
                skipped += 1
                continue
            if LIMIT and done >= LIMIT:
                src.close()
                progress.close()
                print(f"[extract] PILOTA: limite {LIMIT} raggiunto — "
                      f"{done} pagine in {time.time() - t_start:.1f}s "
                      f"({(time.time() - t_start) / max(done, 1):.2f} s/pag)")
                return 0
            t0 = time.time()
            entry = {"doc": stem, "page": pno}
            try:
                one = fitz.open()
                one.insert_pdf(src, from_page=pno - 1, to_page=pno - 1)
                tmp = doc_out / "_tmp_page.pdf"
                one.save(tmp)
                one.close()
                res = converter.convert(str(tmp))
                md = res.document.export_to_markdown()
                md_path.write_text(md, encoding="utf-8")   # checkpoint immediato
                tmp.unlink(missing_ok=True)
                entry.update(ok=True, seconds=round(time.time() - t0, 2),
                             chars=len(md))
                done += 1
            except Exception as exc:  # mai silenzio: fallimento per-pagina loggato
                entry.update(ok=False, seconds=round(time.time() - t0, 2),
                             error=f"{type(exc).__name__}: {exc}")
                failed += 1
            progress.write(json.dumps(entry, ensure_ascii=False) + "\n")
            progress.flush()                               # checkpoint report
            if (done + failed) % 25 == 0:
                el = time.time() - t_start
                print(f"[extract] {done + skipped + failed}/{total} "
                      f"(ok={done} skip={skipped} fail={failed}) "
                      f"{el / max(done + failed, 1):.2f} s/pag", flush=True)
        src.close()

    progress.close()
    el = time.time() - t_start
    print(f"[extract] FINE: totale={total} ok={done} gia'_presenti={skipped} "
          f"falliti={failed} in {el / 60:.1f} min")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
