"""Fase 1 — Fallback OCR nativo per scansioni pure (Microtask 3.2, punto 2).

Le pagine scan puro (no text-layer) falliscono il grounding by-design: nessun
codice e' ancorabile, quindi nessuna riga viene validata. Per non perdere interi
cataloghi scansionati, questo modulo genera un text-layer "invisibile" usando
l'OCR nativo di Apple Silicon (framework Vision via `ocrmac`), molto piu'
veloce ed efficiente di Tesseract su Mac M1 Pro.

Il testo OCR non sostituisce MAI l'estrazione di GLM-OCR (il VLM resta la
fonte strutturata): fornisce solo la base testuale per il grounding deterministico,
cosi' il Fail-Closed puo' validare i codici estratti dal modello contro l'OCR nativo.

Import lazy di `ocrmac` e `PIL`: il modulo si importa anche senza le dipendenze
installate, cosi' i test di invarianti (che usano il FakeOcr) non le richiedono.
Fail-Closed: se l'OCR fallisce, la pagina resta senza text-layer e il grounding
scarta tutto (nessuna riga fabbricata entra in DB).
"""

from __future__ import annotations

import io
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class OcrProvider(Protocol):
    """Port per il fallback OCR: da immagine di pagina -> testo grezzo."""

    def extract_text(self, page) -> str:
        """Estrae testo da una pagina fitz.Page (renderizza + OCR).

        Ritorna il testo grezzo concatenato, o stringa vuota se l'OCR fallisce.
        """
        ...


class FakeOcr:
    """OCR fittizio per i test di invarianti: ritorna testo pre-programmato."""

    def __init__(self, text: str = "CODICE001 DESCRIZIONE 12.50"):
        self._text = text

    def extract_text(self, page) -> str:
        return self._text


class VisionOcr:
    """Adapter reale: usa il framework Vision nativo di macOS via `ocrmac`.

    Su Apple Silicon e' ~10x piu' veloce di Tesseract e non richiede binary
    esterni. Renderizza la pagina a DPI configurabile -> PIL Image -> OCR.
    Import lazy: il modulo si importa anche senza ocrmac/Pillow installati.
    """

    def __init__(self, dpi: int = 200, min_confidence: float = 0.5):
        self._dpi = dpi
        self._min_confidence = min_confidence

    def _load_ocrmac(self):
        from ocrmac import ocrmac  # lazy: pesante (framework Vision)

        return ocrmac

    def _load_pil(self):
        from PIL import Image  # lazy

        return Image

    def extract_text(self, page) -> str:
        """Renderizza la pagina e fa OCR. Fail-closed: errore -> stringa vuota."""
        try:
            Image = self._load_pil()
            ocrmac = self._load_ocrmac()

            pix = page.get_pixmap(dpi=self._dpi)
            img = Image.open(io.BytesIO(pix.tobytes("png")))

            results = ocrmac.OCR(img).recognize()
            # Filtra per confidence e concatena il testo
            texts = [r[0] for r in results if r[1] >= self._min_confidence]
            text = " ".join(texts)

            logger.info(
                "vision_ocr: %d elementi (conf>=%.1f), %d chars estratti",
                len(results),
                self._min_confidence,
                len(text),
            )
            return text
        except Exception as exc:
            logger.error("vision_ocr: fallito — pagina senza text-layer OCR: %s", exc)
            return ""


def needs_ocr(page) -> bool:
    """True se la pagina ha un text-layer insufficiente per il grounding.

    Una pagina "scan puro" ha chars < NTL_CHARS (50) del page_classifier.
    In quel caso il grounding verbatim non ha codici su cui ancorare: serve OCR.
    """
    text = page.get_text("text")
    chars = len("".join(text.split()))
    return chars < 50
