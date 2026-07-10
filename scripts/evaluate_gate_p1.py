"""Gate P1 — Valutazione estrazione tabelle su vere scansioni (riutilizzabile).

Due modi, entrambi read-only (nessuna scrittura in DB: `grounding.partition` e' puro):

  --discover   Trova le pagine "vere scansione + tabella" del corpus (needs_ocr +
               is_table_page) e le ordina per "table-ness" via OCR nativo (quanti
               token codice/prezzo). Serve a capire SE esistono listini scansionati
               (nel corpus attuale: no — sono esplosi/copertine, vedi 01b decision).

  --gate       Esegue GLM-OCR su pagine bersaglio (default o passate a CLI) con
               grounding OCR come verita' indipendente. Riporta, per pagina:
               proposte / validate / respinte dal guardrail + campioni. Dimostra
               che il guardrail deterministico respinge fail-safe l'output non
               ancorato del VLM.

Uso:
    uv run scripts/evaluate_gate_p1.py --discover
    uv run scripts/evaluate_gate_p1.py --gate                       # target di default
    uv run scripts/evaluate_gate_p1.py --gate --page File.pdf:59    # target espliciti

Contesto: la decisione di GATE P1 (GO) e i numeri sono in
docs/piano_implementazione/01b_gate_p1_decision.md.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402

from src.ingestion.extraction.extractor import PageRef  # noqa: E402
from src.ingestion.extraction.glm_ocr import GlmOcrExtractor  # noqa: E402
from src.ingestion.extraction.grounding import partition  # noqa: E402
from src.ingestion.extraction.schema import RigaListino  # noqa: E402
from src.ingestion.extraction.table_detect import is_table_page  # noqa: E402
from src.ingestion.lang_detect import detect_lang  # noqa: E402
from src.ingestion.ocr_fallback import VisionOcr, needs_ocr  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gate_p1")

CORPUS = Path("corpus")
OUTPUT = Path("output/gate_p1")

# token codice (>=4 char, contiene cifra) e prezzo europeo (12,50)
_CODE = re.compile(r"^(?=.*\d)[0-9A-Za-z][0-9A-Za-z./\-]{3,}$")
_PRICE = re.compile(r"^\d{1,4}[.,]\d{2}$")

# Bersagli di default: le pagine-scansione piu' "table-like" del corpus attuale
# (in pratica esplosi/pagine tecniche: nessun listino scansionato esiste qui).
DEFAULT_TARGETS = [
    ("opacmare_catalogo_ricambi_idraulica.pdf", 59),
    ("kbs_gastrotechnik_20322006.pdf", 4),
    ("branson_4520R_parts.pdf", 5),
    ("serrmac_macchine_utensili_catalogo_2026.pdf", 20),
]


def _resolve(name: str) -> Path | None:
    hits = list(CORPUS.rglob(name))
    return hits[0] if hits else None


def discover(sample_per_doc: int = 1) -> list[dict]:
    """Trova e ordina le pagine scansione+tabella per table-ness (OCR nativo)."""
    ocr = VisionOcr(dpi=150, min_confidence=0.4)
    per_doc: dict[str, tuple[str, int]] = {}
    for pdf in sorted(CORPUS.rglob("*.pdf")):
        try:
            doc = fitz.open(str(pdf))
        except Exception:
            continue
        found = 0
        for i, pg in enumerate(doc, start=1):
            if needs_ocr(pg) and is_table_page(pg).is_table:
                per_doc.setdefault(pdf.name, (str(pdf), i))
                found += 1
                if found >= sample_per_doc:
                    break
        doc.close()

    rows: list[dict] = []
    for name, (path, pno) in per_doc.items():
        doc = fitz.open(path)
        txt = ocr.extract_text(doc.load_page(pno - 1))
        doc.close()
        toks = txt.split()
        n_code = sum(1 for t in toks if _CODE.match(t))
        n_price = sum(1 for t in toks if _PRICE.match(t))
        rows.append({
            "doc": name, "page": pno, "lang": detect_lang(txt) if txt else "?",
            "ocr_words": len(toks), "codes": n_code, "prices": n_price,
            "score": n_code + n_price * 2, "sample": " ".join(toks[:24]),
        })
    rows.sort(key=lambda r: -r["score"])
    return rows


def run_gate(targets: list[tuple[str, int]], *, max_new_tokens: int = 1024) -> list[dict]:
    """GLM-OCR + grounding OCR su pagine bersaglio. Read-only. Fail-safe check."""
    ocr = VisionOcr(dpi=200, min_confidence=0.4)
    report: list[dict] = []
    for name, pno in targets:
        path = _resolve(name)
        if path is None:
            report.append({"doc": name, "error": "not found"})
            continue
        doc = fitz.open(str(path))
        ocr_text = ocr.extract_text(doc.load_page(pno - 1))
        ex = GlmOcrExtractor(doc, dpi=200, device="mps", max_new_tokens=max_new_tokens)
        t0 = time.perf_counter()
        grezze = ex.extract(PageRef(name, pno))
        dt = time.perf_counter() - t0
        righe = [RigaListino.from_grezza(g, doc_stem=name, page=pno) for g in grezze]
        valide, scarti = partition(righe, ocr_text)
        report.append({
            "doc": name, "page": pno, "lang": detect_lang(ocr_text) if ocr_text else "?",
            "ocr_words": len(ocr_text.split()), "vlm_time_s": round(dt, 1),
            "vlm_proposed": len(grezze), "grounding_validated": len(valide),
            "grounding_rejected": len(scarti),
            "sample_validated": [
                {"cod": r.codice_originale, "prezzo": str(r.prezzo) if r.prezzo else None}
                for r in valide[:6]
            ],
            "sample_rejected": [
                {"cod": s.codice_originale, "motivo": s.motivo.value} for s in scarti[:6]
            ],
        })
        ex.unload()
        doc.close()
        logger.info("[%s] %s p%d: proposed=%d validated=%d rejected=%d (%.1fs)",
                    report[-1]["lang"], name, pno, len(grezze), len(valide), len(scarti), dt)
    return report


def _parse_page(spec: str) -> tuple[str, int]:
    name, _, page = spec.rpartition(":")
    return name, int(page)


def main() -> int:
    ap = argparse.ArgumentParser(description="Gate P1 — valutazione estrazione tabelle")
    ap.add_argument("--discover", action="store_true", help="scopri le scansioni-tabella del corpus")
    ap.add_argument("--gate", action="store_true", help="esegui GLM-OCR + guardrail sui bersagli")
    ap.add_argument("--page", action="append", default=[], metavar="File.pdf:N",
                    help="bersaglio esplicito (ripetibile); default = DEFAULT_TARGETS")
    args = ap.parse_args()
    if not (args.discover or args.gate):
        ap.error("specifica --discover e/o --gate")

    OUTPUT.mkdir(parents=True, exist_ok=True)
    if args.discover:
        rows = discover()
        (OUTPUT / "discover.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
        logger.info("DISCOVER: %d documenti; top table-ness:", len(rows))
        for r in rows[:12]:
            logger.info("  score=%d [%s] %s p%d codes=%d prices=%d",
                        r["score"], r["lang"], r["doc"], r["page"], r["codes"], r["prices"])
    if args.gate:
        targets = [_parse_page(p) for p in args.page] or DEFAULT_TARGETS
        report = run_gate(targets)
        (OUTPUT / "gate.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
        logger.info("GATE: report scritto in %s", OUTPUT / "gate.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
