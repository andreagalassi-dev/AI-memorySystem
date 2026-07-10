"""Gate P1 — Spike estrazione visiva con GLM-OCR su 3 PDF campione.

Sostituisce il FakeExtractor con GlmOcrExtractor (transformers + MPS) e lancia
la pipeline di estrazione su 3 documenti rappresentativi del corpus:

  1. Connectwell (Listino born-digital) — verifica Fast-Dispatch + estrazione pulita
  2. Mamoli (Scan puro worst-case) — verifica OCR fallback + grounding su text-layer OCR
  3. NordCap (Esploso raster) — verifica capacita' VLM di leggere callout/tabelle

PROTEZIONE SSOT: tutte le scritture avvengono dentro una transazione con ROLLBACK
esplicito. Nessuna riga sopravvive nel DB di produzione.

Uso:
    uv run scripts/spike_gate_p1_glm.py
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
import psycopg  # noqa: E402

from src.backend.clients.postgres_client import conninfo  # noqa: E402
from src.ingestion.extraction.load import init_schema  # noqa: E402
from src.ingestion.extraction.glm_ocr import GlmOcrExtractor  # noqa: E402
from src.ingestion.fast_dispatch import DocumentDispatch, Route, dispatch_pdf  # noqa: E402
from src.ingestion.ocr_fallback import VisionOcr, needs_ocr  # noqa: E402
from src.ingestion.extraction.extractor import PageRef  # noqa: E402
from src.ingestion.extraction.grounding import partition  # noqa: E402
from src.ingestion.extraction.schema import RigaListino  # noqa: E402
from src.ingestion.extraction.table_detect import is_table_page  # noqa: E402
from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("spike_glm")

SCHEMA = "estrazione"
CORPUS = Path("corpus")
OUTPUT = Path("output/spike/gate_p1_glm")

MODEL_NAME = "zai-org/GLM-OCR"

MODEL_NAME = "zai-org/GLM-OCR"
MAX_PAGES_PER_PDF = 5

SAMPLE_PDFS = [
    ("Listino born-digital", CORPUS / "price_lists" / "connectwell_electronics_pricelist_2026.pdf"),
    ("Scan puro worst-case", CORPUS / "original_dataset" / "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf"),
    ("Esploso raster", CORPUS / "original_dataset" / "NordCap_Esploso_Ricambi_DE.pdf"),
]


class _RollbackSignal(Exception):
    """Segnale interno per forzare il rollback della transazione di spike."""


def process_one_pdf(label, pdf_path, extractor, multiplexer, conn, ocr):
    report = {"label": label, "pdf": str(pdf_path), "pages": []}
    if not pdf_path.exists():
        report["error"] = f"PDF non trovato: {pdf_path}"
        return report

    pdf_started = time.perf_counter()
    doc_stem = pdf_path.stem
    dispatch = dispatch_pdf(str(pdf_path), extract_text=False)
    if MAX_PAGES_PER_PDF and MAX_PAGES_PER_PDF < dispatch.n_pages:
        dispatch = DocumentDispatch(
            filepath=dispatch.filepath,
            born_digital=dispatch.born_digital,
            n_pages=MAX_PAGES_PER_PDF,
            pages=dispatch.pages[:MAX_PAGES_PER_PDF],
            fast_pages=sum(1 for p in dispatch.pages[:MAX_PAGES_PER_PDF] if p.route is Route.FAST_TEXT),
            heavy_pages=sum(1 for p in dispatch.pages[:MAX_PAGES_PER_PDF] if p.route is not Route.FAST_TEXT),
        )
    logger.info("[%s] %s: %d pagine FAST=%d HEAVY=%d",
                label, doc_stem, dispatch.n_pages, dispatch.fast_pages, dispatch.heavy_pages)

    doc = fitz.open(str(pdf_path))
    total_valide = total_scarti = total_vlm_proposed = 0

    try:
        with conn.transaction():
            for pd in dispatch.pages:
                page_no = pd.page
                page_report = {"page": page_no, "route": pd.route.value}

                if pd.route is Route.FAST_TEXT:
                    page_report["skipped"] = "FAST_TEXT (bypass AI)"
                    report["pages"].append(page_report)
                    continue

                page = doc.load_page(page_no - 1)
                page_text = page.get_text("text")
                ocr_used = False
                if needs_ocr(page) and ocr is not None:
                    ocr_text = ocr.extract_text(page)
                    if ocr_text:
                        page_text = ocr_text
                        ocr_used = True
                        page_report["ocr_fallback"] = True

                verdict = is_table_page(page)
                if not verdict.is_table and not ocr_used:
                    page_report["skipped"] = f"HEAVY non-tabella ({verdict.reason})"
                    report["pages"].append(page_report)
                    continue

                multiplexer.use(MODEL_NAME)
                t_extract = time.perf_counter()
                grezze = extractor.extract(PageRef(doc_stem, page_no))
                extract_elapsed = time.perf_counter() - t_extract

                righe = [RigaListino.from_grezza(g, doc_stem=doc_stem, page=page_no) for g in grezze]
                valide, scarti = partition(righe, page_text)

                total_valide += len(valide)
                total_scarti += len(scarti)
                total_vlm_proposed += len(grezze)

                page_report.update({
                    "route_detail": "HEAVY_TABLE",
                    "vlm_proposed": len(grezze),
                    "grounding_validated": len(valide),
                    "grounding_rejected": len(scarti),
                    "vlm_time_s": round(extract_elapsed, 3),
                    "table_reason": verdict.reason,
                })
                page_report["sample_validated"] = [
                    {"codice": r.codice_originale, "norm": r.codice_normalizzato,
                     "desc": r.descrizione, "prezzo": str(r.prezzo) if r.prezzo else None}
                    for r in valide[:5]
                ]
                page_report["sample_rejected"] = [
                    {"codice": s.codice_originale, "norm": s.codice_normalizzato_tentato,
                     "motivo": s.motivo.value}
                    for s in scarti[:5]
                ]
                report["pages"].append(page_report)
                logger.info("[%s] page=%d HEAVY_TABLE vlm=%d valide=%d scarti=%d vlm_time=%.2fs",
                            label, page_no, len(grezze), len(valide), len(scarti), extract_elapsed)

            raise _RollbackSignal()
    except _RollbackSignal:
        pass
    finally:
        doc.close()

    elapsed = time.perf_counter() - pdf_started
    report["summary"] = {
        "n_pages_processed": dispatch.n_pages,
        "vlm_proposed_total": total_vlm_proposed,
        "grounding_validated_total": total_valide,
        "grounding_rejected_total": total_scarti,
        "elapsed_s": round(elapsed, 2),
    }
    logger.info("[%s] DONE %s: vlm=%d valide=%d scarti=%d elapsed=%.1fs (ROLLBACK)",
                label, doc_stem, total_vlm_proposed, total_valide, total_scarti, elapsed)
    return report


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    logger.info("=" * 70)
    logger.info("GATE P1 — SPIKE GLM-OCR (zai-org/GLM-OCR, transformers+MPS)")
    logger.info("=" * 70)

    for label, p in SAMPLE_PDFS:
        if not p.exists():
            logger.error("PDF mancante: %s (%s)", p, label)
            return 1

    try:
        conn = psycopg.connect(conninfo())
    except Exception as exc:
        logger.error("Postgres non raggiungibile: %s", exc)
        return 1
    init_schema(conn, schema=SCHEMA)

    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)
    ocr = VisionOcr(dpi=200, min_confidence=0.5)

    all_reports = []
    spike_started = time.perf_counter()
    spike_ok = True
    markdown_samples = []

    for label, pdf_path in SAMPLE_PDFS:
        logger.info("=" * 60)
        logger.info("PDF: %s (%s)", pdf_path.name, label)
        doc = fitz.open(str(pdf_path))
        extractor = GlmOcrExtractor(
            doc, model_name=MODEL_NAME, multiplexer=mux, dpi=200, device="mps")
        try:
            rep = process_one_pdf(label, pdf_path, extractor, mux, conn, ocr)
            all_reports.append(rep)
            markdown_samples.extend(extractor.markdown_outputs[:2])
            if "error" in rep:
                spike_ok = False
        except Exception as exc:
            logger.error("[%s] ERRORE FATALE: %s", label, exc, exc_info=True)
            all_reports.append({"label": label, "pdf": str(pdf_path), "error": str(exc)})
            spike_ok = False
            break
        finally:
            extractor.unload()
            doc.close()

    spike_elapsed = time.perf_counter() - spike_started

    report_path = OUTPUT / "spike_gate_p1_glm_report.json"
    full_report = {
        "model": MODEL_NAME,
        "device": "mps",
        "spike_ok": spike_ok,
        "elapsed_s": round(spike_elapsed, 2),
        "multiplexer_calls": len(rt.calls),
        "pdfs": all_reports,
        "markdown_samples": markdown_samples[:5],
    }
    report_path.write_text(json.dumps(full_report, indent=2, ensure_ascii=False), encoding="utf-8")

    logger.info("=" * 70)
    logger.info("SPIKE GATE P1 (GLM-OCR) — REPORT FINALE")
    logger.info("=" * 70)
    logger.info("Modello: %s su MPS", MODEL_NAME)
    logger.info("Esito spike: %s", "OK" if spike_ok else "FAIL")
    logger.info("Tempo totale: %.1fs", spike_elapsed)
    logger.info("Multiplexer caricamenti: %d", len(rt.calls))
    logger.info("---")
    for rep in all_reports:
        s = rep.get("summary", {})
        if s:
            logger.info("[%s] %s: VLM_proposed=%d grounding_validated=%d grounding_rejected=%d time=%.1fs",
                        rep["label"], Path(rep["pdf"]).stem,
                        s.get("vlm_proposed_total", 0),
                        s.get("grounding_validated_total", 0),
                        s.get("grounding_rejected_total", 0),
                        s.get("elapsed_s", 0))
        elif "error" in rep:
            logger.info("[%s] %s: ERRORE — %s", rep["label"], Path(rep["pdf"]).stem, rep["error"])
    logger.info("---")

    all_times = []
    for rep in all_reports:
        for pg in rep.get("pages", []):
            if "vlm_time_s" in pg:
                all_times.append(pg["vlm_time_s"])
    if all_times:
        logger.info("VLM infer times: n=%d min=%.2fs max=%.2fs avg=%.2fs",
                    len(all_times), min(all_times), max(all_times),
                    sum(all_times) / len(all_times))

    from psycopg import sql
    db_righe = conn.execute(
        sql.SQL("SELECT count(*) FROM {}.righe_listino").format(sql.Identifier(SCHEMA))
    ).fetchone()[0]
    logger.info("DB verifica post-rollback: righe_listino=%d (deve essere 0)", db_righe)
    conn.close()

    return 0 if spike_ok and db_righe == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
