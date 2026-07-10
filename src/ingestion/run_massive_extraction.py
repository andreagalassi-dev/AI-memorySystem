"""Fase 1 — Runner Massivo di estrazione (BLOCCO 3, Microtask 3.1).

Orchestratore CLI che itera sui PDF del corpus applicando la pipeline a triplo binario:

    Fast-Dispatch (classifica) -> [FAST_TEXT: estrai testo, skip AI]
                              -> [HEAVY: ModelMultiplexer carica modello -> table_detect
                                          -> extractor -> grounding -> persist Postgres]

Garantisce le 3 invarianti architetturali:

1. IDEMPOTENZA: il salvataggio per ogni PDF e' transazionale (BEGIN ... COMMIT). Se
   l'estrazione crasha a meta' documento, il rollback annulla TUTTE le scritture di
   quel documento: nessuna riga parziale sopravvive nella SSOT.

2. FAST/HEAVY: ogni pagina e' classificata dal Fast-Dispatch (src/ingestion/fast_dispatch).
   Le pagine FAST_TEXT (born-digital, solo testo) bypassano i modelli pesanti; le
   pagine HEAVY vanno al binario di estrazione. I conteggi sono loggati per audit.

3. WATCHDOG: prima di chiamare l'extractor, il runner invoca il ModelMultiplexer per
   garantire che il modello corretto sia in RAM. L'eviction del modello precedente e'
   gestita dal Multiplexer (LRU) e loggata da src.serving.multiplexer.

Design: docs/superpowers/specs/2026-07-09-fase1.2-estrazione-massiva-design.md +
piano_definitivo_fase1.md (BLOCCO 3).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import fitz
import psycopg
from psycopg import sql as pgsql
from psycopg.types.json import Jsonb as _Jsonb

from src.ingestion.extraction.extractor import PageRef, TableExtractor
from src.ingestion.extraction.grounding import partition
from src.ingestion.extraction.load import DEFAULT_SCHEMA
from src.ingestion.extraction.schema import RigaListino, Scarto
from src.ingestion.extraction.table_detect import is_table_page
from src.ingestion.fast_dispatch import Route, dispatch_pdf
from src.serving.multiplexer import ModelMultiplexer

logger = logging.getLogger(__name__)

# SQL per le scritture transazionali (senza commit interno: gestito dal runner).
# Riutilizza lo stesso schema di load.py ma senza i commit espliciti, cosi' il
# runner puo' avvolgere l'intero documento in una singola transazione atomica.

_UPSERT_RIGHE_TX = pgsql.SQL(
    """
    INSERT INTO {schema}.righe_listino
        (doc_stem, page, codice_normalizzato, codice_originale, descrizione, prezzo, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (doc_stem, page, codice_normalizzato) DO UPDATE SET
        codice_originale = EXCLUDED.codice_originale,
        descrizione      = EXCLUDED.descrizione,
        prezzo           = EXCLUDED.prezzo,
        updated_at       = now()
    """
)

_INSERT_SCARTI_TX = pgsql.SQL(
    """
    INSERT INTO {schema}.scarti_estrazione
        (doc_stem, page, codice_originale, codice_normalizzato_tentato, payload_raw, motivo)
    VALUES (%s, %s, %s, %s, %s, %s)
    """
)


def _persist_tx(
    conn: psycopg.Connection,
    valide: list[RigaListino],
    scarti: list[Scarto],
    *,
    schema: str = DEFAULT_SCHEMA,
) -> dict:
    """Persiste righe + scarti SENZA commit (gestito dalla transazione del runner).

    A differenza di `load.persist` (che fa commit interno), questa variante e'
    pensata per girare dentro un blocco `conn.transaction()`: nessun commit
    esplicito, cosi' un crash a meta' documento fa rollback di tutto.
    """
    n_valide = 0
    if valide:
        q = _UPSERT_RIGHE_TX.format(schema=pgsql.Identifier(schema))
        params = [
            (r.doc_stem, r.page, r.codice_normalizzato, r.codice_originale, r.descrizione, r.prezzo)
            for r in valide
        ]
        with conn.cursor() as cur:
            cur.executemany(q, params)
        n_valide = len(valide)

    n_scarti = 0
    if scarti:
        q = _INSERT_SCARTI_TX.format(schema=pgsql.Identifier(schema))
        params = [
            (
                s.doc_stem,
                s.page,
                s.codice_originale,
                s.codice_normalizzato_tentato,
                _Jsonb(s.payload_raw) if s.payload_raw is not None else None,
                s.motivo.value,
            )
            for s in scarti
        ]
        with conn.cursor() as cur:
            cur.executemany(q, params)
        n_scarti = len(scarti)

    return {"righe_upsert": n_valide, "scarti": n_scarti}


@dataclass(frozen=True)
class MassivePageOutcome:
    """Esito di UNA pagina nel runner massivo."""

    page: int
    route: str  # "FAST_TEXT" | "HEAVY_TABLE" | "HEAVY_NON_TABLE"
    n_valide: int = 0
    n_scarti: int = 0
    reason: str = ""


@dataclass(frozen=True)
class MassiveDocumentOutcome:
    """Riepilogo del documento processato dal runner massivo."""

    doc_stem: str
    n_pages: int
    fast_pages: int
    heavy_pages: int
    table_pages: int
    righe_upsert: int
    scarti: int
    elapsed_s: float
    pages: list[MassivePageOutcome] = field(default_factory=list)


def run_document_massive(
    filepath: str,
    conn: psycopg.Connection,
    extractor: TableExtractor,
    multiplexer: ModelMultiplexer,
    model_name: str,
    schema: str = DEFAULT_SCHEMA,
    dpi: int = 200,
) -> MassiveDocumentOutcome:
    """Processa UN PDF attraverso la pipeline a triplo binario.

    Args:
      filepath: percorso del PDF sorgente.
      conn: connessione psycopg3 alla SSOT (iniettata).
      extractor: port di estrazione tabelle (FakeExtractor nei test; DeepSeek in prod).
      multiplexer: watchdog che gestisce load/unload dei modelli in RAM.
      model_name: nome del modello da caricare via multiplexer per l'estrazione HEAVY.
      schema: schema Postgres di destinazione (override per test).
      dpi: DPI per il rendering delle pagine HEAVY (default 200).

    Ritorna: MassiveDocumentOutcome con conteggi aggregati e esito per-pagina.

    Fail-Closed: se l'extractor solleva, l'intera transazione del documento fa rollback.
    """
    started = time.perf_counter()
    pdf_path = Path(filepath)
    doc_stem = pdf_path.stem

    logger.info("runner: START doc_stem=%s filepath=%s", doc_stem, filepath)

    # 1. Fast-Dispatch: classifica ogni pagina (born-digital solo-testo vs HEAVY).
    dispatch = dispatch_pdf(filepath, extract_text=False)
    fast_pages = dispatch.fast_pages
    heavy_pages = dispatch.heavy_pages
    logger.info(
        "runner: doc_stem=%s pagine=%d FAST=%d HEAVY=%d born_digital=%s",
        doc_stem,
        dispatch.n_pages,
        fast_pages,
        heavy_pages,
        dispatch.born_digital,
    )

    # 2. Apri il PDF per il rendering e l'estrazione testo (servira' per HEAVY).
    doc = fitz.open(filepath)

    page_outcomes: list[MassivePageOutcome] = []
    tot_valide = tot_scarti = table_pages = 0

    try:
        # 3. Transazione atomica per-documento: BEGIN ... COMMIT.
        # Se l'extractor crasha, il rollback annulla tutte le scritture del documento.
        # _persist_tx fa le INSERT SENZA commit: il commit scatta solo se il blocco
        # esce senza eccezioni (psycopg3 context manager).
        with conn.transaction():
            for pd in dispatch.pages:
                page_no = pd.page

                # --- FAST_TEXT: born-digital solo-testo, skip AI ---
                if pd.route is Route.FAST_TEXT:
                    page_outcomes.append(
                        MassivePageOutcome(page=page_no, route="FAST_TEXT", reason=pd.reason)
                    )
                    logger.info(
                        "runner: doc_stem=%s page=%d FAST_TEXT (bypass AI)", doc_stem, page_no
                    )
                    continue

                # --- HEAVY: pagina con immagini/tabelle -> binario estrazione ---
                page = doc.load_page(page_no - 1)

                # table_detect: salta le pagine HEAVY che non sono tabelle
                verdict = is_table_page(page)
                if not verdict.is_table:
                    page_outcomes.append(
                        MassivePageOutcome(
                            page=page_no, route="HEAVY_NON_TABLE", reason=verdict.reason
                        )
                    )
                    logger.info(
                        "runner: doc_stem=%s page=%d HEAVY non-tabella (%s)",
                        doc_stem,
                        page_no,
                        verdict.reason,
                    )
                    continue

                table_pages += 1

                # WATCHDOG: garantisce che il modello sia in RAM prima di estrarre.
                # Il multiplexer scarica i modelli inattivi (eviction LRU) se necessario.
                eff_ctx = multiplexer.use(model_name)
                logger.info(
                    "runner: doc_stem=%s page=%d HEAVY_TABLE model=%s ctx=%d",
                    doc_stem,
                    page_no,
                    model_name,
                    eff_ctx,
                )

                # Estrazione (porta iniettata) + grounding + persist
                grezze = extractor.extract(PageRef(doc_stem, page_no))
                righe = [
                    RigaListino.from_grezza(g, doc_stem=doc_stem, page=page_no) for g in grezze
                ]

                # Grounding: il text-layer e' la fonte di verita' INDIPENDENTE.
                # Le scansioni pure (text-layer vuoto) falliscono correttamente qui.
                page_text = page.get_text("text")
                valide, scarti = partition(righe, page_text)
                summary = _persist_tx(conn, valide, scarti, schema=schema)

                tot_valide += summary["righe_upsert"]
                tot_scarti += summary["scarti"]
                page_outcomes.append(
                    MassivePageOutcome(
                        page=page_no,
                        route="HEAVY_TABLE",
                        n_valide=summary["righe_upsert"],
                        n_scarti=summary["scarti"],
                        reason=verdict.reason,
                    )
                )

    except Exception:
        logger.error("runner: doc_stem=%s ERRORE — rollback transazione", doc_stem, exc_info=True)
        raise
    finally:
        doc.close()

    elapsed = time.perf_counter() - started
    result = MassiveDocumentOutcome(
        doc_stem=doc_stem,
        n_pages=dispatch.n_pages,
        fast_pages=fast_pages,
        heavy_pages=heavy_pages,
        table_pages=table_pages,
        righe_upsert=tot_valide,
        scarti=tot_scarti,
        elapsed_s=round(elapsed, 3),
        pages=page_outcomes,
    )
    logger.info(
        "runner: END doc_stem=%s pagine=%d FAST=%d HEAVY=%d tabelle=%d righe=%d "
        "scarti=%d elapsed=%.3fs",
        doc_stem,
        result.n_pages,
        fast_pages,
        heavy_pages,
        table_pages,
        tot_valide,
        tot_scarti,
        elapsed,
    )
    return result
