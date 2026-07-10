"""Fase 1.3 — Port del modello linguistico (Cervello Unificatore) + FakeLLM.

L'LLM primario (Gemma 4 / Nemotron via MLX) sta DIETRO il Port `LanguageModel`,
come GLM-OCR dietro `TableExtractor` (1.2) e Qwen3-VL dietro `VisualEmbedder`
(1.1). Cosi' il grafo — sintesi narrativa e rendering delle righe SQL — si sviluppa
e testa contro un `FakeLLM` deterministico, senza accendere il modello (niente MLX,
niente download pesi, niente Watchdog nei test).

Contratto minimale e agnostico al backend: `generate(system, user) -> str`. L'adapter
reale `MLXModel` (mlx-lm) arriva a server acceso; qui vivono il Port e il Fake.

Fail-closed: il Fake puo' essere configurato per SOLLEVARE, cosi' i test verificano
che il grafo degradi a "Sistema non disponibile" invece di inghiottire l'errore.

Design: docs/superpowers/specs/2026-07-09-fase1.3-cervello-unificatore-design.md.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable


@runtime_checkable
class LanguageModel(Protocol):
    """Contratto del modello linguistico, indipendente dal backend di inferenza."""

    def generate(self, system: str, user: str) -> str:
        ...


class FakeLLM:
    """Modello deterministico per i test: risposta fissa, handler o errore.

    - `response`: stringa restituita sempre (default "").
    - `handler`: funzione `(system, user) -> str` per risposte dipendenti dall'input.
    - `error`: eccezione da SOLLEVARE a ogni chiamata (test del fail-closed).
    Registra le chiamate in `calls` per gli assert; `handler` ha precedenza su
    `response`.
    """

    def __init__(self, response: str = "", *,
                 handler: Callable[[str, str], str] | None = None,
                 error: Exception | None = None):
        self._response = response
        self._handler = handler
        self._error = error
        self.calls: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if self._error is not None:
            raise self._error
        if self._handler is not None:
            return self._handler(system, user)
        return self._response
