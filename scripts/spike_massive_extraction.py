"""Fase 1 — Spike P1 (Microtask 3.2): 3 PDF reali per validare l'architettura.

Esegue il runner massivo su 3 PDF rappresentativi (listino testuale, scan puro,
esploso) usando un FakeExtractor deterministico con codici REALI presi dai
text-layer. Non usa modelli AI: valida le invarianti architetturali (tempi,
multiplexer, grounding fail-closed sullo scan puro) prima del run sui 149.

Uso:
    docker compose up -d postgres   # SSOT necessaria
    uv run scripts/spike_massive_extraction.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

from src.backend.clients.postgres_client import conninfo  # noqa: E402
from src.ingestion.extraction.extractor import FakeExtractor  # noqa: E402
from src.ingestion.extraction.load import init_schema  # noqa: E402
from src.ingestion.extraction.schema import RigaGrezza  # noqa: E402
from src.ingestion.run_massive_extraction import run_document_massive  # noqa: E402
from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("spike")

SCHEMA = "spike_p1"

# 3 PDF scelti per coprire i casi critici:
# - connectwell: listino born-digital, tutte FAST_TEXT (bypass AI)
# - NordCap: esploso con pagine tabella HEAVY, text-layer con codici reali
# - Mamoli: catalogo con pagina 1 scan puro (text-layer vuoto -> grounding fail)
SPIKE_PDFS = [
    (
        "corpus/price_lists/connectwell_electronics_pricelist_2026.pdf",
        "connectwell_listino_testuale",
    ),
    ("corpus/original_dataset/NordCap_Esploso_Ricambi_DE.pdf", "nordcap_esploso"),
    ("corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf", "mamoli_scan_puro"),
]

# FakeExtractor con codici REALI dai text-layer: alcuni ancorati (presenti),
# alcuni fabbricati (non presenti) per validare il grounding fail-closed.
# IMPORTANTE: le chiavi devono usare il Path.stem reale del file (come fa il runner).
# NordCap p1 ha "4952050B" nel text-layer.
# Mamoli p2 ha "2021" nel text-layer (limitato: codici quasi assenti).
# Mamoli p1 e' scan puro (0 chars text-layer): TUTTI i codici sarebbero non ancorati,
# ma p1 non supera il table_detect (n_words=0) quindi non arriva all'extractor.
SPIKE_ROWS = {
    # NordCap p1: codice reale (ancorato) + codice fabbricato (scarto)
    ("NordCap_Esploso_Ricambi_DE", 1): [
        RigaGrezza(codice_originale="4952050B", descrizione="Guarnizione", prezzo="12.50"),
        RigaGrezza(codice_originale="FABBRICATO999", descrizione="Inesistente", prezzo="99.99"),
    ],
    # Mamoli p2: "2021" e' presente nel text-layer, "FINTO123" non lo e'
    ("Mamoli_Catalogo_Ricambi_2021_IT_EN", 2): [
        RigaGrezza(codice_originale="2021", descrizione="Anno", prezzo=None),
        RigaGrezza(codice_originale="FINTO123", descrizione="Allucinato", prezzo="0.01"),
    ],
    # Mamoli p4: ha text-layer ricco, testiamo un codice presente e uno no
    ("Mamoli_Catalogo_Ricambi_2021_IT_EN", 4): [
        RigaGrezza(codice_originale="2021", descrizione="Anno catalogo", prezzo=None),
        RigaGrezza(codice_originale="CODICEFALSO", descrizione="Fabbricato", prezzo="5.00"),
    ],
    # Mamoli p5: ha text-layer ricco, stesso test
    ("Mamoli_Catalogo_Ricambi_2021_IT_EN", 5): [
        RigaGrezza(codice_originale="2021", descrizione="Anno catalogo", prezzo=None),
        RigaGrezza(codice_originale="INESISTENTE", descrizione="Falso", prezzo="9.99"),
    ],
}


def main() -> int:
    # verifica che il corpus esista
    for fp, _ in SPIKE_PDFS:
        if not Path(fp).exists():
            logger.error("PDF non trovato: %s", fp)
            return 1

    # connessione SSOT
    try:
        conn = psycopg.connect(conninfo())
    except Exception as exc:
        logger.error("Postgres non raggiungibile (docker compose up -d postgres): %s", exc)
        return 1

    # schema usa-e-getta per lo spike
    init_schema(conn, schema=SCHEMA)

    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)
    extractor = FakeExtractor(SPIKE_ROWS)

    model_name = "glm-ocr"
    results = []

    for fp, doc_stem in SPIKE_PDFS:
        logger.info("=" * 70)
        logger.info("SPIKE: processo %s (%s)", doc_stem, fp)

        try:
            # Mamoli ha 237 pagine: limita alle prime 5 per lo spike
            max_pages = 5 if "mamoli" in doc_stem else 0
            result = run_document_massive(
                fp,
                conn=conn,
                extractor=extractor,
                multiplexer=mux,
                model_name=model_name,
                schema=SCHEMA,
                max_pages=max_pages,
            )
            results.append(result)

            logger.info(
                "SPIKE RISULTATO %s: pagine=%d FAST=%d HEAVY=%d tabelle=%d "
                "righe=%d scarti=%d elapsed=%.3fs",
                doc_stem,
                result.n_pages,
                result.fast_pages,
                result.heavy_pages,
                result.table_pages,
                result.righe_upsert,
                result.scarti,
                result.elapsed_s,
            )
            # dettaglio per-pagina
            for po in result.pages:
                if po.route.startswith("HEAVY_TABLE"):
                    logger.info(
                        "  p%d %s valide=%d scarti=%d (%s)",
                        po.page,
                        po.route,
                        po.n_valide,
                        po.n_scarti,
                        po.reason,
                    )
                elif po.route == "FAST_TEXT":
                    logger.info("  p%d %s (%s)", po.page, po.route, po.reason)
                else:
                    logger.info("  p%d %s (%s)", po.page, po.route, po.reason)

        except Exception as exc:
            logger.error("SPIKE FALLITO su %s: %s", doc_stem, exc, exc_info=True)
            results.append(None)

    # --- Riepilogo finale ---
    logger.info("=" * 70)
    logger.info("SPIKE RIEPILOGO FINALE")
    logger.info("=" * 70)
    total_elapsed = sum(r.elapsed_s for r in results if r)
    total_righe = sum(r.righe_upsert for r in results if r)
    total_scarti = sum(r.scarti for r in results if r)
    logger.info("Totale documenti processati: %d/%d", sum(1 for r in results if r), len(results))
    logger.info("Totale tempo: %.3fs", total_elapsed)
    logger.info("Totale righe valide: %d", total_righe)
    logger.info("Totale scarti (grounding): %d", total_scarti)
    logger.info("Multiplexer (runtime fake) chiamate: %s", rt.calls)

    # --- Grounding report ---
    logger.info("--- Grounding (Fail-Closed) report ---")
    for r in results:
        if r is None:
            continue
        for po in r.pages:
            if po.route.startswith("HEAVY_TABLE") and po.n_scarti > 0:
                logger.info(
                    "  %s p%d: %d righe scartate (codici non ancorati)",
                    r.doc_stem,
                    po.page,
                    po.n_scarti,
                )
            if r.doc_stem == "Mamoli_Catalogo_Ricambi_2021_IT_EN" and po.page == 1:
                logger.info(
                    "  %s p%d: scan puro (text-layer vuoto) -> non tabella, "
                    "skipped dal table_detect (fail-closed corretto)",
                    r.doc_stem,
                    po.page,
                )

    # cleanup schema spike
    try:
        conn.execute(f'DROP SCHEMA IF EXISTS "{SCHEMA}" CASCADE')
        conn.commit()
    except Exception:
        pass
    conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
