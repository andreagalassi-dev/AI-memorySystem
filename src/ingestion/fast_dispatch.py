"""Fase 1.0 — Fast-Dispatch (Puro Testo).

Micro-step di classificazione all'ingresso della pipeline di ingestion a triplo
binario (docs/PIANO-IMPLEMENTAZIONE.md §Fase 1). Per ogni pagina di un PDF decide
UNA rotta ed estrae il testo al volo SOLO dove la pagina e' born-digital e
solo-testo, bypassando i modelli pesanti (PixelRAG 1.1, DeepSeek-OCR 1.2) e
risparmiando compute/RAM. Si ferma all'interfaccia di routing: non chunka, non
embedda, non chiama modelli.

Riuso, non reinvenzione: la decisione born-digital-solo-testo e' una lettura pura
del classificatore P2.5 gia' calibrato (`src/ingestion/page_classifier.py`), zero
soglie nuove. `no_text_layer` esclude le scansioni mute (non born-digital); `dual`/
`grafica` escludono immagini/tabelle rasterizzate; `low_content` esclude le pagine
vuote.

Principio Fail-Closed: il modulo RIFIUTA di inghiottire gli errori. File corrotti o
protetti da password -> eccezione tipizzata rilanciata + log strutturato. Nessun
crash silenzioso. E' responsabilita' del chiamante batch (worker/watchdog)
avvolgere la chiamata in try/except per non interrompere il ciclo massivo.

Design: docs/superpowers/specs/2026-07-09-fase1.0-fast-dispatch-design.md.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum

import fitz  # PyMuPDF

from src.ingestion.page_classifier import PageClass, classify, page_features

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------ eccezioni


class FastDispatchError(Exception):
    """Base: il documento non e' processabile dal Fast-Dispatch."""


class EncryptedPdfError(FastDispatchError):
    """Il PDF e' protetto da password (fitz: `doc.needs_pass`)."""


class CorruptPdfError(FastDispatchError):
    """Il file non e' un PDF valido o e' danneggiato (apertura fitz fallita)."""


# ------------------------------------------------------------------ contratto


class Route(Enum):
    """Rotta di dispatch di una pagina."""

    FAST_TEXT = "fast_text"  # born-digital solo-testo: testo estratto qui, no AI
    HEAVY = "heavy"          # -> binari 1.1 (PixelRAG) + 1.2 (DeepSeek-OCR)


@dataclass(frozen=True)
class PageDispatch:
    """Esito di dispatch di UNA pagina (1-based)."""

    page: int
    route: Route
    text: str | None       # valorizzato SOLO se route == FAST_TEXT
    reason: str            # auditabile: eredita PageClass.reason
    features: dict         # feature PyMuPDF grezze (audit)


@dataclass(frozen=True)
class DocumentDispatch:
    """Esito di dispatch dell'intero documento."""

    filepath: str
    born_digital: bool     # almeno una pagina ha un text-layer estraibile
    n_pages: int
    pages: list[PageDispatch]
    fast_pages: int
    heavy_pages: int


# ------------------------------------------------------------------- decisione


def route_for(pc: PageClass) -> Route:
    """Mappa una classe di pagina (P2.5) alla rotta di dispatch. Pura, testabile.

    FAST_TEXT solo se born-digital E solo-testo: testuale, non mista (`dual`),
    con text-layer (`not no_text_layer`), non vuota (`not low_content`).
    """
    if (
        pc.label == "testuale"
        and not pc.dual
        and not pc.no_text_layer
        and not pc.low_content
    ):
        return Route.FAST_TEXT
    return Route.HEAVY


# ------------------------------------------------------------------ core sync


def dispatch_pdf(filepath: str, *, extract_text: bool = True) -> DocumentDispatch:
    """Classifica un PDF pagina-per-pagina e instrada (Fase 1.0).

    Args:
      filepath: percorso del PDF sorgente.
      extract_text: se True estrae il testo delle pagine FAST_TEXT (default);
        se False classifica soltanto (dry-run rapido, nessuna estrazione).

    Ritorna: DocumentDispatch con l'esito per-pagina e i conteggi aggregati.

    Solleva:
      EncryptedPdfError: il PDF e' protetto da password.
      CorruptPdfError: il file e' danneggiato o non e' un PDF valido.
    """
    try:
        doc = fitz.open(filepath)
    except Exception as exc:  # apertura fallita: file mancante/danneggiato/non-PDF
        logger.error("fast_dispatch: apertura fallita filepath=%s err=%s",
                     filepath, exc)
        raise CorruptPdfError(f"impossibile aprire il PDF: {filepath}") from exc

    try:
        if doc.needs_pass:
            logger.error("fast_dispatch: PDF cifrato filepath=%s", filepath)
            raise EncryptedPdfError(f"PDF protetto da password: {filepath}")

        pages: list[PageDispatch] = []
        born_digital = False
        for i, page in enumerate(doc, start=1):
            feat = page_features(page)
            pc = classify(feat)
            route = route_for(pc)
            # born-digital: la pagina ha un text-layer estraibile (non scansione muta)
            if not pc.no_text_layer and feat["chars"] > 0:
                born_digital = True
            text = None
            if route is Route.FAST_TEXT and extract_text:
                text = page.get_text("text")
            pages.append(PageDispatch(page=i, route=route, text=text,
                                      reason=pc.reason, features=feat))
    except FastDispatchError:
        raise
    except Exception as exc:  # danno strutturale emerso durante la lettura pagine
        logger.error("fast_dispatch: lettura pagine fallita filepath=%s err=%s",
                     filepath, exc)
        raise CorruptPdfError(f"lettura del PDF fallita: {filepath}") from exc
    finally:
        doc.close()

    fast = sum(1 for p in pages if p.route is Route.FAST_TEXT)
    result = DocumentDispatch(
        filepath=filepath, born_digital=born_digital, n_pages=len(pages),
        pages=pages, fast_pages=fast, heavy_pages=len(pages) - fast,
    )
    logger.info(
        "fast_dispatch: filepath=%s pagine=%d fast=%d heavy=%d born_digital=%s",
        filepath, result.n_pages, result.fast_pages, result.heavy_pages,
        result.born_digital,
    )
    return result


# ----------------------------------------------------------------- wrapper async


async def dispatch_pdf_async(filepath: str, *,
                             extract_text: bool = True) -> DocumentDispatch:
    """Wrapper async di `dispatch_pdf`: offload su thread (fitz e' CPU-bound/bloccante).

    Da usare dall'event loop (futura API Chainlit/FastAPI) per non bloccarlo.
    Propaga le stesse eccezioni tipizzate del core sincrono.
    """
    return await asyncio.to_thread(dispatch_pdf, filepath, extract_text=extract_text)
