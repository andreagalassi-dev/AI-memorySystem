"""P2.6 — Chunking dell'intero corpus estratto (output/extraction -> chunk jsonl).

Per ogni documento estratto: profilo brand dal registro, lingua rilevata sul
testo aggregato, chunk per pagina (table_row + text) con guardia
`code_not_in_source`. Le pagine Mamoli P060/P231 sono ESCLUSE (gia' coperte
dai chunk-spike validati col GT in output/spike/chunks — niente duplicati).

Output: output/ingestion/chunks/<doc>.jsonl, _rejects.jsonl, _report.json.
Exit 1 se: nessuna estrazione trovata, o violazioni guardia non zero
(i reject sono ammessi e contati; la violazione fatale e' un chunk emesso
che violi la guardia — impossibile per costruzione, verificato comunque).

Uso: uv run scripts/chunk_corpus.py
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingestion.brands import resolve  # noqa: E402
from src.ingestion.codes import canonical  # noqa: E402
from src.ingestion.corpus_chunks import chunk_page  # noqa: E402
from src.ingestion.lang_detect import detect_lang  # noqa: E402

EXTRACTION = ROOT / "output" / "extraction"
OUT = ROOT / "output" / "ingestion" / "chunks"
CLASSES = ROOT / "output" / "spike" / "page_classes"
# gia' coperte dai chunk-spike GT-validati (P2.2): mai duplicare
SKIP_PAGES = {("Mamoli_Catalogo_Ricambi_2021_IT_EN", 60),
              ("Mamoli_Catalogo_Ricambi_2021_IT_EN", 231)}


def doc_category(stem: str) -> str:
    for jl in CLASSES.glob("*.jsonl"):
        if jl.stem == stem:
            first = json.loads(jl.open(encoding="utf-8").readline())
            return first["cat"]
    return "unknown"


def main() -> int:
    doc_dirs = sorted(d for d in EXTRACTION.iterdir()
                      if d.is_dir()) if EXTRACTION.exists() else []
    if not doc_dirs:
        print(f"[corpus-chunk] FATALE: nessuna estrazione in {EXTRACTION}")
        return 1
    OUT.mkdir(parents=True, exist_ok=True)

    report: dict = {}
    totals: Counter = Counter()
    all_rejects: list[dict] = []

    for doc_dir in doc_dirs:
        stem = doc_dir.name
        pages = sorted(doc_dir.glob("p*.md"))
        if not pages:
            continue
        profile = resolve(stem)
        cat = doc_category(stem)
        full_text = "\n".join(p.read_text(encoding="utf-8") for p in pages)
        lang = detect_lang(full_text)

        ocr_marker = EXTRACTION / "_ocr_pages.json"
        ocr_pages = (set(json.loads(ocr_marker.read_text()).get(stem, []))
                     if ocr_marker.exists() else set())

        doc_chunks, doc_rejects = [], []
        for md_path in pages:
            pno = int(md_path.stem[1:])
            if (stem, pno) in SKIP_PAGES:
                continue
            res = chunk_page(stem, cat, pno, md_path.read_text(encoding="utf-8"),
                             profile, lang)
            if pno in ocr_pages:
                # fonte OCR (corsia P2.6 per le tabelle-mute): la guardia
                # code_not_in_source e' circolare su testo OCR -> il guardrail
                # P3 deve poter degradare questi chunk a "verifica manuale"
                for c in res["chunks"]:
                    c.warnings.append("ocr_source")
                    if c.status != "repaired":  # 'repaired' resta tracciabile
                        c.status = "degraded"
            doc_chunks.extend(res["chunks"])
            doc_rejects.extend(res["rejects"])

        # guardia verificata anche a valle (difesa in profondita'); i chunk
        # 'repaired' (debito §4) sono ESENTI per costruzione — il loro codice
        # e' la correzione di un glifo/split assente dal sorgente — ma devono
        # portare il warning fuzzy_code che li cappa a MEDIA
        for c in doc_chunks:
            if c.code and c.status != "repaired":
                page_md = (doc_dir / f"p{c.page:04d}.md").read_text(encoding="utf-8")
                assert canonical(c.code) in canonical(page_md), c.chunk_id
            elif c.status == "repaired":
                assert "fuzzy_code" in c.warnings, c.chunk_id

        with (OUT / f"{stem}.jsonl").open("w", encoding="utf-8") as fh:
            for c in doc_chunks:
                fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
        all_rejects.extend(doc_rejects)
        counts = Counter(c.type for c in doc_chunks)
        counts["with_code"] = sum(1 for c in doc_chunks if c.code)
        counts["repaired"] = sum(1 for c in doc_chunks if c.status == "repaired")
        report[stem] = {"cat": cat, "lang": lang, "profile": profile["_name"],
                        "pages": len(pages), "chunks": dict(counts),
                        "rejects": dict(Counter(r["reason"] for r in doc_rejects))}
        totals.update(counts)
        totals["rejects"] += len(doc_rejects)

    with (OUT / "_rejects.jsonl").open("w", encoding="utf-8") as fh:
        for r in all_rejects:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (OUT / "_report.json").write_text(
        json.dumps({"totali": dict(totals), "per_doc": report},
                   indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"[corpus-chunk] documenti={len(report)} "
          f"table_row={totals['table_row']} text={totals['text']} "
          f"con_codice={totals['with_code']} riparati={totals['repaired']} "
          f"rejects={totals['rejects']}")
    reasons = Counter(r["reason"] for r in all_rejects)
    print(f"[corpus-chunk] reject per motivo: {dict(reasons) or '{}'}")
    langs = Counter(v["lang"] for v in report.values())
    print(f"[corpus-chunk] lingue per documento: {dict(langs)}")
    print(f"[corpus-chunk] OK — output in {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
