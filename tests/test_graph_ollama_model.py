"""Fase 1.3 — Adapter `OllamaModel` sul Port `LanguageModel` (render STRUCTURED).

Unit test DB/Ollama-free: la funzione di chat e' INIETTATA, cosi' si verifica il
contratto (implementa il Port, delega alla chat, propaga gli errori = fail-closed)
senza accendere il modello. L'osservazione col modello reale e' il grounding loop
manuale (vedi handoff), non un unit test.
"""

from __future__ import annotations

import pytest

from src.backend.clients.ollama_client import OllamaModel
from src.graph.llm import LanguageModel


def test_implementa_il_port_languagemodel():
    assert isinstance(OllamaModel(_chat=lambda s, u: ""), LanguageModel)


def test_generate_delega_alla_chat_iniettata():
    seen = {}

    def fake_chat(system: str, user: str) -> str:
        seen["system"], seen["user"] = system, user
        return "12,50 € (listino_x, pag. 3)"

    out = OllamaModel(_chat=fake_chat).generate("SYS", "USER")
    assert out == "12,50 € (listino_x, pag. 3)"
    assert seen == {"system": "SYS", "user": "USER"}


def test_errore_del_modello_propaga_fail_closed():
    # il render_node fa il try/except -> system_error; l'adapter NON inghiotte
    def boom(system: str, user: str) -> str:
        raise RuntimeError("Ollama giù")

    with pytest.raises(RuntimeError, match="Ollama giù"):
        OllamaModel(_chat=boom).generate("SYS", "USER")
