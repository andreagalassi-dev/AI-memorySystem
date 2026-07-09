"""Fase 1.2 — Adapter DeepSeek-OCR-2: implementazione reale del port TableExtractor.

Chiude il cerchio del binario di estrazione: renderizza la pagina in PNG e la manda
al modello multimodale (DeepSeek-OCR-2 servito via endpoint OpenAI-compat), con
schema-enforcement Instructor a valle. Rispetta ESATTAMENTE il contratto
`TableExtractor.extract(PageRef) -> list[RigaGrezza]`, quindi e' drop-in
nell'orchestratore al posto del `FakeExtractor`, senza toccarlo.

Rendering: `PageRef` porta solo (doc_stem, page); l'adapter INCAPSULA il
`fitz.Document`, cosi' `extract` sa da dove renderizzare. Pagina -> pixmap a DPI
configurabile (default 200: leggibilita' celle/codici vs budget token/RAM del VLM
su Apple Silicon) -> PNG -> data-URI base64.

Fail-Closed: rendering fallito -> `PageRenderError` rilanciata (mai un'immagine
vuota al modello). L'invenzione di struttura la blocca `extra="forbid"` (enforce);
l'invenzione di valore la blocca il grounding a valle (che usa il text-layer come
fonte di verita' INDIPENDENTE dall'LLM: le scansioni pure, prive di text-layer,
falliscono correttamente il grounding finche' non avremo un OCR deterministico
indipendente).

Design: docs/superpowers/specs/2026-07-09-fase1.2-estrazione-massiva-design.md.
"""

from __future__ import annotations

import base64
import logging

import fitz
import instructor

from src.ingestion.extraction.enforce import enforce_page
from src.ingestion.extraction.extractor import PageRef
from src.ingestion.extraction.schema import RigaGrezza

logger = logging.getLogger(__name__)

_INSTRUCTION = (
    "Questa immagine e' una pagina di listino/tabella ricambi. Estrai tutte le righe "
    "leggibili: per ciascuna il codice_originale (cosi' com'e' stampato), la "
    "descrizione e il prezzo se presenti. Non inventare righe o valori non visibili."
)


class PageRenderError(Exception):
    """Il rendering della pagina in immagine e' fallito (fail-closed)."""


class DeepSeekExtractor:
    """Adapter `TableExtractor` su DeepSeek-OCR-2 (endpoint OpenAI-compat via Instructor).

    Il client Instructor e il modello sono INIETTATI (costruiti dal chiamante da
    `pydantic-settings` con `enforce.build_client`): l'adapter non conosce segreti
    ne' endpoint. Mockabile nei test sostituendo il client.
    """

    def __init__(self, doc: fitz.Document, client: instructor.Instructor, *, model: str,
                 dpi: int = 200, max_retries: int = 2, instruction: str = _INSTRUCTION):
        self._doc = doc
        self._client = client
        self._model = model
        self._dpi = dpi
        self._max_retries = max_retries
        self._instruction = instruction

    def extract(self, ref: PageRef) -> list[RigaGrezza]:
        """Renderizza la pagina e la fa estrarre dal modello, con schema-enforcement."""
        content = [
            {"type": "text", "text": self._instruction},
            {"type": "image_url", "image_url": {"url": self._render_data_uri(ref.page)}},
        ]
        return enforce_page(self._client, content, model=self._model,
                            max_retries=self._max_retries)

    def _render_data_uri(self, page_no: int) -> str:
        """Pagina (1-based) -> PNG a DPI -> data-URI base64. Fail-closed sul rendering."""
        try:
            page = self._doc.load_page(page_no - 1)
            png = page.get_pixmap(dpi=self._dpi).tobytes("png")
        except Exception as exc:  # pagina fuori range / documento danneggiato
            logger.error("deepseek: rendering pagina %d fallito: %s", page_no, exc)
            raise PageRenderError(f"rendering pagina {page_no} fallito") from exc
        b64 = base64.b64encode(png).decode("ascii")
        return f"data:image/png;base64,{b64}"
