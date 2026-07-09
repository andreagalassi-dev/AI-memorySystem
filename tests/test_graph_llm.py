"""Fase 1.3 — Test del Port LanguageModel e del FakeLLM (no MLX).

Verifica il contratto, le tre modalita' del Fake (risposta fissa / handler / errore)
e la registrazione delle chiamate per gli assert del grafo.
"""

from __future__ import annotations

import pytest

from src.graph.llm import FakeLLM, LanguageModel


class TestContratto:
    def test_fake_e_un_language_model(self):
        assert isinstance(FakeLLM(), LanguageModel)


class TestModalita:
    def test_risposta_fissa(self):
        llm = FakeLLM("il prezzo e' 12,50")
        assert llm.generate("sys", "quanto costa?") == "il prezzo e' 12,50"

    def test_default_stringa_vuota(self):
        assert FakeLLM().generate("s", "u") == ""

    def test_handler_dipende_dall_input(self):
        llm = FakeLLM(handler=lambda system, user: f"eco:{user}")
        assert llm.generate("sys", "ciao") == "eco:ciao"

    def test_handler_ha_precedenza_su_response(self):
        llm = FakeLLM("fissa", handler=lambda s, u: "dinamica")
        assert llm.generate("s", "u") == "dinamica"

    def test_errore_sollevato(self):
        # fail-closed: il grafo deve poter osservare l'eccezione, non un valore muto
        llm = FakeLLM(error=RuntimeError("modello non disponibile"))
        with pytest.raises(RuntimeError, match="non disponibile"):
            llm.generate("s", "u")


class TestRegistrazione:
    def test_registra_le_chiamate(self):
        llm = FakeLLM("ok")
        llm.generate("sys1", "u1")
        llm.generate("sys2", "u2")
        assert llm.calls == [("sys1", "u1"), ("sys2", "u2")]
