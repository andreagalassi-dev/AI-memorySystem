"""Script D-B.4-stage1: Re-estrazione pilota in PRODUZIONE con nuovo estrattore Docling sui 4 documenti pilota."""

from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

from src.backend.clients.postgres_client import conninfo  # noqa: E402
from src.ingestion.extraction.extractor import FakeExtractor  # noqa: E402
from src.ingestion.extraction.load import init_schema  # noqa: E402
from src.ingestion.ocr_fallback import VisionOcr  # noqa: E402
from src.ingestion.run_massive_extraction import MassiveDocumentOutcome, run_document_massive, get_text_extractor_factory  # noqa: E402
from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("reextract_db4_pilot")

SCHEMA = "estrazione"

TARGET_PDFS = [
    Path("corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf"),
    Path("corpus/hardware_fasteners/ivm_edilizia_catalistino_2026.pdf"),
    Path("corpus/engines/kohler_marine_tp6954.pdf"),
    Path("corpus/price_lists/nsk_cuscinetti_listino_2025.pdf"),
]

def main() -> int:
    logger.info("Avvio re-estrazione DB4-stage1 su %d PDF target", len(TARGET_PDFS))

    try:
        conn = psycopg.connect(conninfo())
    except Exception as exc:
        logger.error("Postgres non raggiungibile: %s", exc)
        return 1

    init_schema(conn, schema=SCHEMA)

    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)
    ocr = VisionOcr(dpi=200, min_confidence=0.5)
    extractor = FakeExtractor({})
    
    text_extractor_factory = get_text_extractor_factory()

    results: list[MassiveDocumentOutcome | None] = []
    total_started = time.perf_counter()
    total_fast = total_heavy = total_tables = total_righe = total_scarti = 0
    errors: list[str] = []

    for i, pdf in enumerate(TARGET_PDFS, 1):
        logger.info("=" * 60)
        logger.info("[%d/%d] %s", i, len(TARGET_PDFS), pdf.name)

        doc_stem = pdf.stem

        try:
            with conn.transaction():
                # DELETE per evitare UPSERT additivi sui codici
                logger.info("DELETE old data for %s", doc_stem)
                conn.execute(f"DELETE FROM {SCHEMA}.righe_listino WHERE doc_stem = %s", (doc_stem,))
                conn.execute(f"DELETE FROM {SCHEMA}.scarti_estrazione WHERE doc_stem = %s", (doc_stem,))
                
                result = run_document_massive(
                    str(pdf),
                    conn=conn,
                    extractor=extractor,
                    multiplexer=mux,
                    model_name="glm-ocr",
                    schema=SCHEMA,
                    ocr=ocr,
                    text_extractor_factory=text_extractor_factory,
                )
            # Transaction commits here if no exception
            
            results.append(result)
            total_fast += result.fast_pages
            total_heavy += result.heavy_pages
            total_tables += result.table_pages
            total_righe += result.righe_upsert
            total_scarti += result.scarti

            logger.info(
                "[%d/%d] DONE %s: pagine=%d FAST=%d HEAVY=%d tabelle=%d "
                "righe=%d scarti=%d elapsed=%.1fs",
                i,
                len(TARGET_PDFS),
                pdf.name,
                result.n_pages,
                result.fast_pages,
                result.heavy_pages,
                result.table_pages,
                result.righe_upsert,
                result.scarti,
                result.elapsed_s,
            )

        except Exception as exc:
            logger.error("[%d/%d] ERRORE su %s: %s", i, len(TARGET_PDFS), pdf.name, exc, exc_info=True)
            errors.append(f"{pdf.name}: {exc}")
            results.append(None)


    total_elapsed = time.perf_counter() - total_started

    logger.info("=" * 70)
    logger.info("RE-ESTRAZIONE D-B.4-stage1 — REPORT FINALE")
    logger.info("=" * 70)
    logger.info("Documenti processati: %d/%d", sum(1 for r in results if r), len(TARGET_PDFS))
    logger.info("Errori: %d", len(errors))
    for e in errors:
        logger.info("  - %s", e)
    logger.info("Pagine totali: %d", sum(r.n_pages for r in results if r))
    logger.info("Pagine FAST_TEXT: %d", total_fast)
    logger.info("Pagine HEAVY: %d", total_heavy)
    logger.info("Pagine tabella (estratte): %d", total_tables)
    logger.info("Righe valide inserite in DB: %d", total_righe)
    logger.info("Righe scartate: %d", total_scarti)
    logger.info("Tempo totale: %.1fs", total_elapsed)

    conn.close()
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
