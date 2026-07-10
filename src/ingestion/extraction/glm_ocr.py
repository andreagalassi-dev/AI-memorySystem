"""Fase 1.2 — Adapter GLM-OCR per l'estrazione tabelle su Apple Silicon (MPS).

Sostituisce il vecchio DeepSeekExtractor (endpoint OpenAI-compat, incompatibile
su Apple Silicon per via di custom_code CUDA-specifico). GLM-OCR (zai-org/GLM-OCR)
e' un modello multimodale da 1B params che usa classi transformers STANDARD
(`AutoModelForImageTextToText` + `AutoProcessor`): nessun `trust_remote_code`,
nessun `custom_code`, nessun `flash_attn`. Gira nativamente su MPS.

Model card: https://huggingface.co/zai-org/GLM-OCR
- 1B params BF16 (~2GB): leggero, entra nei 32GB unificati insieme a Gemma 4
- Prompt "Table Recognition:" built-in per il riconoscimento tabelle
- Throughput: 1.86 pagine/sec (misurato su server di reference)

Implementa il Port `TableExtractor` (come `FakeExtractor`), cosi' e' drop-in
nel runner massivo. Il flusso:

  1. multiplexer.use(model_name)  -> eviction LRU dei modelli inattivi (Watchdog)
  2. AutoModelForImageTextToText.from_pretrained(...).to("mps")
  3. render pagina -> PNG -> chat_template + generate -> testo markdown
  4. parse markdown -> list[RigaGrezza] (validazione Pydantic, fail-closed)
  5. cleanup: del model + gc.collect() + torch.mps.empty_cache()

Schema-enforcement: GLM-OCR e' un OCR (output markdown), non un LLM strutturato.
Non si usa `instructor` (serve un endpoint OpenAI-compat). Si fa parsing
deterministico del markdown -> `RigaGrezza` validate da Pydantic. L'invenzione
di VALORE la intercetta il grounding a valle (fonte di verita' = text-layer/OCR).
"""

from __future__ import annotations

import gc
import logging
import re
import tempfile
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path

import fitz

from src.ingestion.extraction.extractor import PageRef
from src.ingestion.extraction.schema import RigaGrezza

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "zai-org/GLM-OCR"

# Prompt del model card: "Table Recognition:" per il riconoscimento tabelle.
_TABLE_PROMPT = "Table Recognition:"


class GlmOcrExtractor:
    """Adapter `TableExtractor` su GLM-OCR via transformers + MPS.

    Carica il modello torch su MPS (Apple Silicon) dopo aver chiesto al
    `ModelMultiplexer` di evictare i modelli inattivi. Fail-closed: errori
    di caricamento/inferenza vengono propagati (mai inghiottiti).

    Il caricamento e' LAZY (alla prima `extract`): costruire l'adapter non
    scarica pesi ne' tocca torch. Il cleanup esplicito (`unload`) rilascia i
    pesi e svuota la cache MPS (anti-OOM su memoria unificata 32GB).
    """

    def __init__(
        self,
        doc: fitz.Document,
        *,
        model_name: str = DEFAULT_MODEL,
        multiplexer: object | None = None,
        dpi: int = 200,
        device: str = "mps",
        prompt: str = _TABLE_PROMPT,
        max_new_tokens: int = 8192,
    ):
        self._doc = doc
        self.model_name = model_name
        self._multiplexer = multiplexer
        self._dpi = dpi
        self._device = device
        self._prompt = prompt
        self._max_new_tokens = max_new_tokens
        self._model = None
        self._processor = None
        self._loaded = False
        # metriche di spike: tempi di inferenza per pagina (secondi)
        self.infer_times: list[float] = []
        self.markdown_outputs: list[str] = []

    def _ensure_resident(self) -> None:
        """Hook Watchdog: chiede al Multiplexer di garantire la RAM/VRAM.

        `multiplexer.use(model_name)` evicta i modelli inattivi (LRU, es. Gemma 4)
        prima del caricamento del VLM. Idempotente (anti-churn). Se nessun
        multiplexer e' iniettato, no-op (path di test/spike isolato).
        """
        if self._multiplexer is not None:
            self._multiplexer.use(self.model_name)

    def _load_model(self) -> None:
        """Carica modello + processor torch su MPS (lazy).

        GLM-OCR usa classi transformers standard (AutoModelForImageTextToText,
        AutoProcessor): nessun trust_remote_code, nessun custom_code, nessun
        flash_attn. dtype=bfloat16 e' il default del model card.
        """
        if self._loaded:
            return
        import torch
        from transformers import AutoModelForImageTextToText, AutoProcessor

        self._ensure_resident()
        logger.info("glm_ocr: caricamento %s su %s (bfloat16)", self.model_name, self._device)
        self._processor = AutoProcessor.from_pretrained(self.model_name)
        self._model = AutoModelForImageTextToText.from_pretrained(
            self.model_name,
            torch_dtype=torch.bfloat16,
        ).to(self._device).eval()
        self._loaded = True
        logger.info("glm_ocr: modello caricato su %s", self._device)

    def _render_png(self, page_no: int) -> Path:
        """Renderizza la pagina (1-based) in PNG temporaneo. Fail-closed."""
        try:
            page = self._doc.load_page(page_no - 1)
            pix = page.get_pixmap(dpi=self._dpi)
            tmp = Path(tempfile.mkstemp(suffix=".png")[1])
            pix.save(str(tmp))
            return tmp
        except Exception as exc:
            raise RuntimeError(f"rendering pagina {page_no} fallito: {exc}") from exc

    def _infer(self, image_path: Path) -> str:
        """Genera testo dal modello (chat template + generate). Ritorna il markdown.

        Usa il chat template standard di GLM-OCR con il prompt "Table Recognition:".
        Fail-closed: eccezioni propagate (non inghiottite).
        """
        import torch

        t0 = time.perf_counter()
        try:
            message = {
                "role": "user",
                "content": [
                    {"type": "image", "url": str(image_path)},
                    {"type": "text", "text": self._prompt},
                ],
            }
            inputs = self._processor.apply_chat_template(
                [message],
                tokenize=True,
                add_generation_prompt=True,
                return_dict=True,
                return_tensors="pt",
            ).to(self._device)
            inputs.pop("token_type_ids", None)

            with torch.no_grad():
                generated_ids = self._model.generate(
                    **inputs, max_new_tokens=self._max_new_tokens)
            output_text = self._processor.decode(
                generated_ids[0][inputs["input_ids"].shape[1]:],
                skip_special_tokens=False,
            )

            elapsed = time.perf_counter() - t0
            self.infer_times.append(elapsed)
            self.markdown_outputs.append(output_text)
            logger.info("glm_ocr: infer %.2fs, %d chars markdown",
                        elapsed, len(output_text))
            return output_text
        except Exception:
            elapsed = time.perf_counter() - t0
            self.infer_times.append(elapsed)
            raise

    def extract(self, ref: PageRef) -> list[RigaGrezza]:
        """Renderizza la pagina, fa inferenza VLM, parse markdown -> RigaGrezza.

        Fail-closed: errori di caricamento/inferenza vengono propagati;
        errori di parse -> riga saltata con log (non blocca il documento).
        """
        self._load_model()
        png = self._render_png(ref.page)
        try:
            markdown = self._infer(png)
        finally:
            try:
                png.unlink(missing_ok=True)
            except OSError:
                pass
        return parse_markdown_to_righe(markdown)

    def unload(self) -> None:
        """Rilascia i pesi e svuota la cache MPS (anti-OOM memoria unificata).

        Obbligatorio su Apple Silicon: senza questo, i ~2GB del modello restano
        residenti e un caricamento successivo (es. Gemma 4) va in OOM.
        """
        if not self._loaded:
            return
        import torch
        del self._model
        self._model = None
        self._processor = None
        self._loaded = False
        gc.collect()
        if self._device == "mps" and torch.backends.mps.is_available():
            torch.mps.empty_cache()
            logger.info("glm_ocr: pesi rilasciati, cache MPS svuotata")

    def __del__(self):
        try:
            self.unload()
        except Exception:
            pass


# -------------------------------------------------------- parser output VLM

_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_MD_SEP = re.compile(r"^\s*\|[\s\-:|]+\|\s*$")
_HTML_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
_HTML_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.IGNORECASE | re.DOTALL)
_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(s: str) -> str:
    """Rimuove tag HTML e normalizza whitespace (br -> spazio, entity base)."""
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.IGNORECASE)
    s = _HTML_TAG.sub("", s)
    s = s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("&nbsp;", " ").replace("&euro;", "€")
    return " ".join(s.split()).strip()


def parse_markdown_to_righe(markdown: str) -> list[RigaGrezza]:
    """Parser deterministico output VLM -> list[RigaGrezza] (fail-closed soft).

    Gestisce DUE formati di output di GLM-OCR:
    - Tabelle markdown (righe con `|`)
    - Tabelle HTML (<table><tr><td>...)

    Per ogni riga di dati costruisce una `RigaGrezza`. Euristiche colonna
    (listini ricambi): prima cella non-vuota = codice, ultima numerica = prezzo,
    colonne centrali = descrizione.
    Righe non valide (codice vuoto) vengono saltate con log, non sollevate.
    """
    if not markdown or not markdown.strip():
        return []
    righe: list[RigaGrezza] = []

    # --- PATH HTML: <table><tr><td>...
    if "<table" in markdown.lower():
        for row_match in _HTML_ROW.finditer(markdown):
            cells = [_strip_html(c) for c in _HTML_CELL.findall(row_match.group(1))]
            cells = [c for c in cells if c]
            if len(cells) < 2:
                continue
            r = _cells_to_riga(cells)
            if r is not None:
                righe.append(r)
        if righe:
            return righe

    # --- PATH markdown: | col1 | col2 |
    lines = markdown.splitlines()
    in_table = False
    header_seen = False
    for ln in lines:
        if _MD_TABLE_ROW.match(ln):
            if _MD_SEP.match(ln):
                header_seen = True
                continue
            if not header_seen and not in_table:
                in_table = True
                continue  # salta header
            cells = [c.strip() for c in ln.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            r = _cells_to_riga(cells)
            if r is not None:
                righe.append(r)
        else:
            if in_table:
                in_table = False
                header_seen = False
    return righe


def _cells_to_riga(cells: list[str]) -> RigaGrezza | None:
    """Euristica: prima cella non-vuota = codice, ultima numerica = prezzo, resto = descrizione."""
    non_empty = [c for c in cells if c]
    if not non_empty:
        return None
    codice = non_empty[0]
    descrizione = None
    prezzo = None
    # cerca prezzo nell'ultima cella numerica
    for c in reversed(cells):
        p = _try_price(c)
        if p is not None:
            prezzo = p
            break
    # descrizione = celle tra codice e prezzo
    middle = [c for c in cells[1:] if c and _try_price(c) is None]
    if middle:
        descrizione = " ".join(middle)
    try:
        return RigaGrezza(
            codice_originale=codice,
            descrizione=descrizione if descrizione else None,
            prezzo=prezzo,
        )
    except Exception as exc:
        logger.debug("parse: riga scartata (%s): %s", exc, cells)
        return None


def _try_price(s: str) -> "object | None":
    """Tenta di parsare una cella come prezzo (Decimal). None se non numerica."""
    cleaned = s.replace(",", ".").replace("€", "").replace("EUR", "").strip()
    if not cleaned:
        return None
    try:
        return Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None
