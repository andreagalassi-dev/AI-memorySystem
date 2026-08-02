"""Fase 1.2 — Orchestratore del binario di estrazione massiva.

Cuce insieme i pezzi gia' testati in isolamento, pagina per pagina:

    table_detect  ->  extractor  ->  grounding  ->  load (Postgres)

- `table_detect.is_table_page`  : salta le pagine non-tabella (no estrazione inutile).
- `extractor` (PORT iniettato)  : da PageRef a `list[RigaGrezza]`. Il `FakeExtractor`
  sta al posto di "modello + enforce" per l'e2e veloce; l'adapter GLM-OCR reale
  incapsulera' l'inferenza VLM dietro lo stesso Protocol, senza toccare
  l'orchestratore.
- `grounding.partition`         : strato 2 Fail-Closed (codice non ancorato -> scarto).
- `load.persist`                : upsert righe valide + insert scarti nella SSOT.

Fail-Closed end-to-end: solo le righe che superano forma (a monte, nell'extractor)
E grounding verbatim arrivano in `estrazione.righe_listino`; tutto il resto e'
tracciato negli scarti. Nessun dato non validato entra in silenzio.

Il testo di grounding e' il text-layer della pagina (`get_text`); per le scansioni
l'adapter reale fornira' l'OCR grezzo come sorgente — l'orchestratore resta uguale.

Design: docs/superpowers/specs/2026-07-09-fase1.2-estrazione-massiva-design.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

from src.ingestion.extraction.extractor import PageRef, TableExtractor
from src.ingestion.extraction.grounding import partition
from src.ingestion.extraction.load import (
    DEFAULT_SCHEMA,
    init_schema,
    persist,
    warn_intra_page_collisions,
)
from src.ingestion.extraction.schema import RigaListino
from src.ingestion.extraction.table_detect import is_table_page

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PageOutcome:
    """Esito di UNA pagina nel binario di estrazione."""

    page: int
    is_table: bool
    n_valide: int
    n_scarti: int
    reason: str        # ragione del verdetto table_detect (audit)


@dataclass(frozen=True)
class DocumentOutcome:
    """Riepilogo del documento processato dal binario di estrazione."""

    doc_stem: str
    n_pages: int
    table_pages: int
    righe_upsert: int
    scarti: int
    pages: list[PageOutcome]


def run_document(conn: psycopg.Connection, doc, *, doc_stem: str,
                 extractor: TableExtractor, schema: str = DEFAULT_SCHEMA) -> DocumentOutcome:
    """Estrae, valida e persiste le tabelle di UN documento.

    Args:
      conn: connessione psycopg3 alla SSOT (iniettata).
      doc: documento sfogliabile pagina-per-pagina (es. `fitz.Document`).
      doc_stem: identita' logica del documento.
      extractor: port di estrazione (FakeExtractor nei test; adapter GLM-OCR in prod).
      schema: schema Postgres di destinazione (override per l'isolamento dei test).

    Ritorna: `DocumentOutcome` con i conteggi aggregati e l'esito per-pagina.
    """
    init_schema(conn, schema=schema)  # idempotente: garantisce le tabelle

    pages: list[PageOutcome] = []
    table_pages = tot_valide = tot_scarti = 0
    for i, page in enumerate(doc, start=1):
        verdict = is_table_page(page)
        if not verdict.is_table:
            pages.append(PageOutcome(i, False, 0, 0, verdict.reason))
            continue

        table_pages += 1
        grezze = extractor.extract(PageRef(doc_stem, i))
        righe = [RigaListino.from_grezza(g, doc_stem=doc_stem, page=i) for g in grezze]
        valide, scarti = partition(righe, page.get_text("text"))

        # GUARD/osservabilita': collisioni intra-pagina PRIMA dell'upsert (mai mute)
        warn_intra_page_collisions(valide)

        summary = persist(conn, valide, scarti, schema=schema)

        tot_valide += summary["righe_upsert"]
        tot_scarti += summary["scarti"]
        pages.append(PageOutcome(i, True, summary["righe_upsert"], summary["scarti"],
                                 verdict.reason))

    result = DocumentOutcome(doc_stem=doc_stem, n_pages=len(pages), table_pages=table_pages,
                             righe_upsert=tot_valide, scarti=tot_scarti, pages=pages)
    logger.info("pipeline: doc_stem=%s pagine=%d tabelle=%d righe=%d scarti=%d",
                doc_stem, result.n_pages, table_pages, tot_valide, tot_scarti)
    return result
