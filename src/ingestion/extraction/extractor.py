"""Fase 1.2 — Port di estrazione tabelle + Fake per i test.

Il modello reale DeepSeek-OCR-2 sta DIETRO il port `TableExtractor`: enforce,
grounding e load si sviluppano e testano contro un `FakeExtractor` deterministico,
senza modello ne' rete. L'adapter DeepSeek reale (endpoint OpenAI-compat, serving
MLX) sara' un modulo separato che implementa lo stesso Protocol. Coerente con
l'iniezione `extract_fn` gia' usata in `src/ingestion/ingest_one.py`.

Design: docs/superpowers/specs/2026-07-09-fase1.2-estrazione-massiva-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.ingestion.extraction.schema import RigaGrezza


@dataclass(frozen=True)
class PageRef:
    """Riferimento a UNA pagina da estrarre (1-based).

    L'input pixel reale (render della pagina) sara' aggiunto dall'adapter DeepSeek:
    il contratto resta minimale finche' l'interfaccia del modello non e' fissata.
    """

    doc_stem: str
    page: int


@runtime_checkable
class TableExtractor(Protocol):
    """Contratto: da un `PageRef` a righe grezze (non validate, non normalizzate)."""

    def extract(self, ref: PageRef) -> list[RigaGrezza]:
        ...


class FakeExtractor:
    """Estrattore deterministico per i test: righe pre-programmate per pagina.

    Costruito con una mappa {(doc_stem, page) -> righe}. Ritorna le righe della
    pagina richiesta, o lista vuota se la pagina non e' programmata. Puo' emettere
    volutamente righe con codici fabbricati per testare lo scarto del grounding.
    """

    def __init__(self, rows_by_page: dict[tuple[str, int], list[RigaGrezza]] | None = None):
        self._rows: dict[tuple[str, int], list[RigaGrezza]] = dict(rows_by_page or {})

    def extract(self, ref: PageRef) -> list[RigaGrezza]:
        # copia difensiva: il chiamante non deve poter mutare lo stato del fake
        return list(self._rows.get((ref.doc_stem, ref.page), []))
