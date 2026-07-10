"""Fase 1 — Run Massivo Definitivo sui 149 PDF (Microtask 3.3).

Esegue il runner massivo sull'intero corpus con:
- Fast-Dispatch (bypass AI su born-digital solo-testo)
- ModelMultiplexer (watchdog anti-OOM)
- VisionOcr (fallback OCR nativo per scan puro)
- FakeExtractor deterministico (i modelli reali GLM-OCR/Gemma 4 saranno
  integrati a seguire; questo run valida l'architettura end-to-end sui 149 PDF).

Salva i risultati in PostgreSQL (transazioni atomiche per-documento).
Stampa il report finale: righe estratte, scarti, tempi, OOM check.

Uso:
    docker compose up -d postgres
    uv run scripts/run_full_corpus.py
"""

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
from src.ingestion.extraction.text_table import TextTableExtractor  # noqa: E402
from src.ingestion.ocr_fallback import VisionOcr  # noqa: E402
from src.ingestion.run_massive_extraction import MassiveDocumentOutcome, run_document_massive  # noqa: E402
from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("run_full")

SCHEMA = "estrazione"

CORPUS = Path("corpus")


def find_all_pdfs() -> list[Path]:
    """Trova tutti i PDF nel corpus (ricorsivo)."""
    return sorted(CORPUS.rglob("*.pdf"))


def main() -> int:
    pdfs = find_all_pdfs()
    logger.info("Trovati %d PDF nel corpus", len(pdfs))
    if not pdfs:
        logger.error("Nessun PDF trovato in %s", CORPUS)
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
    # FakeExtractor vuoto: nessuna riga pre-programmata. Le pagine tabella
    # verranno identificate dal table_detect, ma l'extractor non emettera'
    # righe (simula il caso reale prima dell'integrazione di GLM-OCR).
    # Questo run valida: Fast-Dispatch, table_detect, OCR fallback, transazioni,
    # e che nessun crash/OOM avviene sull'intero corpus.
    extractor = FakeExtractor({})

    results: list[MassiveDocumentOutcome | None] = []
    total_started = time.perf_counter()
    total_fast = total_heavy = total_tables = total_righe = total_scarti = 0
    total_ocr_pages = 0
    errors: list[str] = []
    oom_events: list[str] = []

    for i, pdf in enumerate(pdfs, 1):
        logger.info("=" * 60)
        logger.info("[%d/%d] %s", i, len(pdfs), pdf.name)

        try:
            result = run_document_massive(
                str(pdf),
                conn=conn,
                extractor=extractor,
                multiplexer=mux,
                model_name="glm-ocr",
                schema=SCHEMA,
                ocr=ocr,
                text_extractor_factory=TextTableExtractor,
            )
            results.append(result)
            total_fast += result.fast_pages
            total_heavy += result.heavy_pages
            total_tables += result.table_pages
            total_righe += result.righe_upsert
            total_scarti += result.scarti

            # conta pagine OCR
            for po in result.pages:
                if "OCR" in po.reason or "ocr" in po.reason.lower():
                    total_ocr_pages += 1

            logger.info(
                "[%d/%d] DONE %s: pagine=%d FAST=%d HEAVY=%d tabelle=%d "
                "righe=%d scarti=%d elapsed=%.1fs",
                i,
                len(pdfs),
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
            logger.error("[%d/%d] ERRORE su %s: %s", i, len(pdfs), pdf.name, exc, exc_info=True)
            errors.append(f"{pdf.name}: {exc}")
            results.append(None)

        # OOM check: se il multiplexer ha fatto piu' caricamenti del previsto
        if len(rt.calls) > len(pdfs) * 2:
            oom_events.append(f"Troppi caricamenti modello su {pdf.name}")

    total_elapsed = time.perf_counter() - total_started

    # --- Report finale ---
    logger.info("=" * 70)
    logger.info("RUN MASSIVO DEFINITIVO — REPORT FINALE")
    logger.info("=" * 70)
    logger.info("Documenti processati: %d/%d", sum(1 for r in results if r), len(pdfs))
    logger.info("Errori: %d", len(errors))
    for e in errors:
        logger.info("  - %s", e)
    logger.info("OOM events: %d", len(oom_events))
    for o in oom_events:
        logger.info("  - %s", o)
    logger.info("---")
    logger.info("Pagine totali: %d", sum(r.n_pages for r in results if r))
    logger.info("Pagine FAST_TEXT (bypass AI): %d", total_fast)
    logger.info("Pagine HEAVY: %d", total_heavy)
    logger.info("Pagine tabella (estratte): %d", total_tables)
    logger.info("Pagine OCR fallback: %d", total_ocr_pages)
    logger.info("---")
    logger.info("Righe valide inserite in DB: %d", total_righe)
    logger.info("Righe scartate (grounding): %d", total_scarti)
    logger.info("---")
    logger.info("Tempo totale: %.1fs (%.1fmin)", total_elapsed, total_elapsed / 60)
    logger.info("Tempo medio per documento: %.1fs", total_elapsed / len(pdfs) if pdfs else 0)
    logger.info("Multiplexer caricamenti modello: %d", len(rt.calls))

    # Verifica righe in DB
    from psycopg import sql

    db_righe = conn.execute(
        sql.SQL("SELECT count(*) FROM {}.righe_listino").format(sql.Identifier(SCHEMA))
    ).fetchone()[0]
    db_scarti = conn.execute(
        sql.SQL("SELECT count(*) FROM {}.scarti_estrazione").format(sql.Identifier(SCHEMA))
    ).fetchone()[0]
    logger.info("---")
    logger.info("DB verifica: righe_listino=%d, scarti_estrazione=%d", db_righe, db_scarti)

    conn.close()
    return 0 if not errors else 2


if __name__ == "__main__":
    sys.exit(main())
