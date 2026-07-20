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
from collections.abc import Callable
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
from src.ingestion.extraction.text_table import reliable_text_layer
from src.ingestion.fast_dispatch import DocumentDispatch, Route, dispatch_pdf
from src.ingestion.ocr_fallback import OcrProvider, needs_ocr
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
    # "FAST_TEXT" | "FAST_TABLE_TEXT" | "HEAVY_TABLE_TEXT" | "HEAVY_TABLE_VLM" | "HEAVY_NON_TABLE"
    route: str
    n_valide: int = 0
    n_scarti: int = 0
    reason: str = ""


def route_table_page(
    page,
    *,
    text_extractor: TableExtractor | None,
    vlm_extractor: TableExtractor,
) -> tuple[TableExtractor, str]:
    """Sceglie l'estrattore per una pagina-tabella HEAVY (regola architetturale).

    Una pagina born-digital (text-layer affidabile) va all'estrattore testuale
    (codici ESATTI): GLM-OCR corromperebbe i codici alfanumerici. Solo le VERE
    scansioni (senza testo nativo) — o l'assenza di un text-extractor iniettato —
    restano sul VLM. Pura e DB-free per l'unit test.

    Ritorna: (estrattore scelto, etichetta di rotta per l'audit).
    """
    if text_extractor is not None and reliable_text_layer(page):
        return text_extractor, "HEAVY_TABLE_TEXT"
    return vlm_extractor, "HEAVY_TABLE_VLM"


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
    max_pages: int = 0,
    ocr: OcrProvider | None = None,
    text_extractor_factory: Callable[[fitz.Document], TableExtractor] | None = None,
) -> MassiveDocumentOutcome:
    """Processa UN PDF attraverso la pipeline a triplo binario.

    Args:
      filepath: percorso del PDF sorgente.
      conn: connessione psycopg3 alla SSOT (iniettata).
      extractor: port di estrazione tabelle (FakeExtractor nei test; GLM-OCR in prod).
      multiplexer: watchdog che gestisce load/unload dei modelli in RAM.
      model_name: nome del modello da caricare via multiplexer per l'estrazione HEAVY.
      schema: schema Postgres di destinazione (override per test).
      dpi: DPI per il rendering delle pagine HEAVY (default 200).
      max_pages: se >0, elabora solo le prime N pagine (spike/test su PDF grandi).
      ocr: provider OCR fallback per pagine senza text-layer (scan puro). Se None,
           le pagine scan puro restano senza text-layer e il grounding scarta tutto.
      text_extractor_factory: costruisce l'estrattore testuale per-documento dal
           `fitz.Document` aperto internamente (es. `TextTableExtractor`). Se dato,
           le pagine-tabella born-digital passano di qua (codici esatti) e GLM-OCR
           resta relegato alle vere scansioni. Se None, tutte le tabelle vanno al VLM.

    Ritorna: MassiveDocumentOutcome con conteggi aggregati e esito per-pagina.

    Fail-Closed: se l'extractor solleva, l'intera transazione del documento fa rollback.
    """
    started = time.perf_counter()
    pdf_path = Path(filepath)
    doc_stem = pdf_path.stem

    logger.info("runner: START doc_stem=%s filepath=%s", doc_stem, filepath)

    # 1. Fast-Dispatch: classifica ogni pagina (born-digital solo-testo vs HEAVY).
    dispatch = dispatch_pdf(filepath, extract_text=False)
    # Limita alle prime max_pages pagine se richiesto (spike su PDF grandi).
    if max_pages and max_pages < dispatch.n_pages:
        dispatch = DocumentDispatch(
            filepath=dispatch.filepath,
            born_digital=dispatch.born_digital,
            n_pages=max_pages,
            pages=dispatch.pages[:max_pages],
            fast_pages=sum(1 for p in dispatch.pages[:max_pages] if p.route is Route.FAST_TEXT),
            heavy_pages=sum(
                1 for p in dispatch.pages[:max_pages] if p.route is not Route.FAST_TEXT
            ),
        )
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
    # Estrattore testuale per-documento (codici esatti sulle born-digital), se richiesto.
    text_extractor = text_extractor_factory(doc) if text_extractor_factory else None

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

                # --- FAST_TEXT: born-digital solo-testo, di solito skip AI ---
                # BUG-FIX C2.a: le tabelle borderless (poche linee vettoriali ma
                # colonne allineate) sono classificate FAST_TEXT dal dispatcher,
                # ma contengono dati strutturati estraibili. Controlliamo con
                # is_table_page (segnale TEXT-BASED, no modelli) e, se positivo,
                # estraiamo come il ramo HEAVY-text (TextTableExtractor).
                if pd.route is Route.FAST_TEXT:
                    ft_page = doc.load_page(page_no - 1)
                    ft_verdict = is_table_page(ft_page)
                    if ft_verdict.is_table and text_extractor is not None:
                        # Tabella borderless born-digital: estrai via text path.
                        table_pages += 1
                        ft_page_text = ft_page.get_text("text")
                        ft_grezze = text_extractor.extract(PageRef(doc_stem, page_no))
                        ft_righe = [
                            RigaListino.from_grezza(g, doc_stem=doc_stem, page=page_no)
                            for g in ft_grezze
                        ]
                        ft_valide, ft_scarti = partition(ft_righe, ft_page_text)
                        ft_summary = _persist_tx(conn, ft_valide, ft_scarti, schema=schema)
                        tot_valide += ft_summary["righe_upsert"]
                        tot_scarti += ft_summary["scarti"]
                        page_outcomes.append(
                            MassivePageOutcome(
                                page=page_no,
                                route="FAST_TABLE_TEXT",
                                n_valide=ft_summary["righe_upsert"],
                                n_scarti=ft_summary["scarti"],
                                reason=ft_verdict.reason,
                            )
                        )
                        logger.info(
                            "runner: doc_stem=%s page=%d FAST_TABLE_TEXT "
                            "(borderless born-digital, %d righe, %d scarti)",
                            doc_stem, page_no,
                            ft_summary["righe_upsert"], ft_summary["scarti"],
                        )
                    else:
                        # Pagina solo-testo: bypass standard.
                        page_outcomes.append(
                            MassivePageOutcome(
                                page=page_no, route="FAST_TEXT", reason=pd.reason,
                            )
                        )
                        logger.info(
                            "runner: doc_stem=%s page=%d FAST_TEXT (bypass AI)",
                            doc_stem, page_no,
                        )
                    continue

                # --- HEAVY: pagina con immagini/tabelle -> binario estrazione ---
                page = doc.load_page(page_no - 1)

                # OCR FALLBACK: se la pagina ha un text-layer insufficiente (scan
                # puro), il table_detect e il grounding non hanno nulla su cui
                # lavorare. L'OCR genera un text-layer "invisibile" che alimenta
                # entrambi. Senza OCR, le pagine scan puro vengono saltate dal
                # table_detect (fail-closed: nessuna riga fabbricata).
                page_text = page.get_text("text")
                ocr_used = False
                if needs_ocr(page) and ocr is not None:
                    ocr_text = ocr.extract_text(page)
                    if ocr_text:
                        page_text = ocr_text
                        ocr_used = True
                        logger.info(
                            "runner: doc_stem=%s page=%d OCR fallback attivato "
                            "(text-layer insufficiente, %d chars da OCR)",
                            doc_stem,
                            page_no,
                            len(ocr_text),
                        )
                    else:
                        logger.warning(
                            "runner: doc_stem=%s page=%d OCR fallback fallito — "
                            "grounding senza text-layer (tutto scartato)",
                            doc_stem,
                            page_no,
                        )

                # table_detect: salta le pagine HEAVY che non sono tabelle.
                # Nota: per le pagine scan puro con OCR, il table_detect usa
                # feature geometriche (linee/colonne) che non dipendono dal
                # text-layer, ma ha una guardia n_words >= MIN_WORDS. Se l'OCR
                # ha prodotto testo ma n_words e' ancora 0 (perche' table_detect
                # legge dal text-layer nativo, non dall'OCR), bypassiamo il check
                # quando abbiamo testo OCR: una pagina HEAVY con OCR text e'
                # candidata all'estrazione.
                verdict = is_table_page(page)
                if not verdict.is_table and not ocr_used:
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

                # ROUTING: born-digital (text-layer affidabile) -> estrattore
                # testuale (codici esatti); scansione vera -> GLM-OCR (VLM).
                chosen, route_label = route_table_page(
                    page, text_extractor=text_extractor, vlm_extractor=extractor
                )

                if chosen is extractor:
                    # WATCHDOG: il modello serve in RAM solo sul percorso VLM.
                    # Il multiplexer evicta i modelli inattivi (LRU) se necessario.
                    eff_ctx = multiplexer.use(model_name)
                    logger.info(
                        "runner: doc_stem=%s page=%d %s model=%s ctx=%d",
                        doc_stem, page_no, route_label, model_name, eff_ctx,
                    )
                else:
                    logger.info(
                        "runner: doc_stem=%s page=%d %s (text-layer nativo, no modello)",
                        doc_stem, page_no, route_label,
                    )

                # Estrazione (porta scelta dal routing) + grounding + persist
                grezze = chosen.extract(PageRef(doc_stem, page_no))
                righe = [
                    RigaListino.from_grezza(g, doc_stem=doc_stem, page=page_no) for g in grezze
                ]

                # Grounding: il text-layer (o l'OCR fallback) e' la fonte di
                # verita' INDIPENDENTE dal modello. I codici estratti dal VLM
                # devono essere ancorati a token presenti nel testo della pagina.
                valide, scarti = partition(righe, page_text)
                summary = _persist_tx(conn, valide, scarti, schema=schema)

                tot_valide += summary["righe_upsert"]
                tot_scarti += summary["scarti"]
                page_outcomes.append(
                    MassivePageOutcome(
                        page=page_no,
                        route=route_label,
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
