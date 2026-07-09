"""Fase 1.2 — Schema-enforcement dell'estrazione (Instructor + Pydantic strict).

Forza il modello (DeepSeek-OCR-2 servito via endpoint OpenAI-compat: LM Studio/
vLLM/Ollama) a produrre ESATTAMENTE `list[RigaGrezza]`: Instructor patcha il client
OpenAI e usa `response_model` per validare l'output con Pydantic, ritentando su
output non-valido fino a `max_retries` e poi **sollevando**. Fail-Closed: nessuna
riga malformata entra in silenzio; l'output imperfetto e' un errore rumoroso, non
un dato accettato a meta'.

Perche' `response_model=list[RigaGrezza]` e non `RigaListino`: il modello e' un
passacarte ignorante. `RigaGrezza` (con `extra="forbid"`) e' cio' che il modello
DEVE produrre; i metadati di `RigaListino` (`codice_normalizzato`, `doc_stem`,
`page`) li calcoliamo NOI a valle (`schema.RigaListino.from_grezza`) e il grounding
li verifica. Non deleghiamo al modello nulla di critico.

L'endpoint e il modello arrivano da `pydantic-settings`, mai hardcoded; il client
e' INIETTATO in `enforce_page`, cosi' i test si agganciano a un LLM mockato senza
rete. Design: docs/superpowers/specs/2026-07-09-fase1.2-estrazione-massiva-design.md.
"""

from __future__ import annotations

import logging

import instructor
from openai import OpenAI

from src.ingestion.extraction.schema import RigaGrezza

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "Sei un estrattore di righe da tabelle e listini ricambi. Estrai SOLO le righe "
    "effettivamente presenti nel testo fornito: non inventare codici, descrizioni o "
    "prezzi. Per ogni riga fornisci codice_originale (obbligatorio, cosi' com'e' nel "
    "documento), descrizione (se presente) e prezzo (se presente). Non aggiungere "
    "campi non richiesti."
)


def build_client(*, base_url: str, api_key: str,
                 mode: instructor.Mode = instructor.Mode.JSON) -> instructor.Instructor:
    """Patcha un client OpenAI-compat con Instructor (default JSON mode, robusto sui
    server locali). base_url/api_key da pydantic-settings, mai hardcoded."""
    return instructor.from_openai(OpenAI(base_url=base_url, api_key=api_key), mode=mode)


def enforce_page(client: instructor.Instructor, page_text: str, *, model: str,
                 max_retries: int = 2) -> list[RigaGrezza]:
    """Estrae e valida le righe di una pagina forzando lo schema `list[RigaGrezza]`.

    Args:
      client: client Instructor-patched (iniettato; mockabile nei test).
      page_text: testo/markdown grezzo della pagina da cui estrarre le righe.
      model: nome del modello sull'endpoint (es. 'deepseek-ocr').
      max_retries: tentativi di ri-generazione su output non-valido prima di sollevare.

    Ritorna: `list[RigaGrezza]` validate (Pydantic strict via `response_model`).

    Solleva: qualsiasi eccezione di Instructor/validazione a retry esauriti viene
      **propagata** (fail-closed): il chiamante decide, ma il modulo non inghiotte
      un output imperfetto.
    """
    rows: list[RigaGrezza] = client.chat.completions.create(
        model=model,
        response_model=list[RigaGrezza],
        max_retries=max_retries,
        messages=[
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": page_text},
        ],
    )
    logger.info("enforce: model=%s righe_validate=%d", model, len(rows))
    return rows
