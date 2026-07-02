"""P2.5 — Classificazione di TUTTE le pagine del corpus (testuale vs grafica).

Per ogni PDF: una riga JSONL per pagina (label, flag dual/no_text_layer/
low_content, feature, reason auditabile). Produce inoltre le LISTE DI ROUTING
per i punti successivi:
  _route_docling.txt  -> pagine testuali + dual (estrazione tabelle P2.6)
  _route_colpali.txt  -> pagine grafiche + dual (embedding visivi P2.7)
  _route_ocr_review.txt -> pagine senza text-layer (decisione OCR a P2.6)

Fail-fast: ogni PDF deve aprirsi, ogni pagina deve ricevere una label,
il conteggio finale deve quadrare col totale del corpus.

Uso: uv run scripts/classify_pages.py
"""

from __future__ import annotations

import json
import sys
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

from src.ingestion.page_classifier import classify, page_features  # noqa: E402

CORPUS = ROOT / "corpus"
OUT = ROOT / "output" / "spike" / "page_classes"


def main() -> int:
    pdfs = sorted(CORPUS.rglob("*.pdf"))
    if not pdfs:
        print(f"[pages] FATALE: nessun PDF in {CORPUS}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    corpus_counts: Counter = Counter()
    per_doc: dict = {}
    routes = {"docling": [], "colpali": [], "ocr_review": []}
    total_pages = 0

    for pdf in pdfs:
        try:
            doc = fitz.open(pdf)
        except Exception as exc:
            print(f"[pages] FATALE: {pdf} non apribile: {exc}")
            return 1
        cat = pdf.parent.name
        lines = []
        counts: Counter = Counter()
        for i in range(len(doc)):
            feats = page_features(doc[i])
            pc = classify(feats)
            ref = f"{cat}/{pdf.name}#p{i + 1}"
            counts[pc.label] += 1
            counts["dual"] += pc.dual
            counts["no_text_layer"] += pc.no_text_layer
            counts["low_content"] += pc.low_content
            if pc.label == "testuale" or pc.dual:
                routes["docling"].append(ref)
            if pc.label == "grafica" or pc.dual:
                routes["colpali"].append(ref)
            if pc.no_text_layer:
                routes["ocr_review"].append(ref)
            lines.append(json.dumps({"doc": pdf.name, "cat": cat, "page": i + 1,
                                     **asdict(pc), **feats}, ensure_ascii=False))
        total_pages += len(doc)
        doc.close()
        (OUT / f"{pdf.stem}.jsonl").write_text("\n".join(lines) + "\n",
                                               encoding="utf-8")
        per_doc[f"{cat}/{pdf.name}"] = dict(counts)
        corpus_counts.update(counts)

    elapsed = time.time() - t0
    for name, refs in routes.items():
        (OUT / f"_route_{name}.txt").write_text("\n".join(refs) + "\n",
                                                encoding="utf-8")
    summary = {
        "pdf": len(pdfs), "pagine": total_pages, "secondi": round(elapsed, 1),
        "corpus": dict(corpus_counts),
        "route_sizes": {k: len(v) for k, v in routes.items()},
        "per_doc": per_doc,
    }
    (OUT / "_summary.json").write_text(json.dumps(summary, indent=2,
                                                  ensure_ascii=False),
                                       encoding="utf-8")

    classified = corpus_counts["testuale"] + corpus_counts["grafica"]
    print(f"[pages] {len(pdfs)} PDF, {total_pages} pagine in {elapsed:.1f}s "
          f"({total_pages / elapsed:.0f} pag/s)")
    print(f"[pages] testuale={corpus_counts['testuale']} "
          f"grafica={corpus_counts['grafica']} dual={corpus_counts['dual']} "
          f"no_text_layer={corpus_counts['no_text_layer']} "
          f"low_content={corpus_counts['low_content']}")
    print(f"[pages] routing: docling={len(routes['docling'])} "
          f"colpali={len(routes['colpali'])} ocr_review={len(routes['ocr_review'])}")
    if classified != total_pages:
        print(f"[pages] FATALE: {classified} label != {total_pages} pagine")
        return 1
    print(f"[pages] OK — output in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
