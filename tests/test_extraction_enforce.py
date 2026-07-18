"""Fase 1.2 — Test dello schema-enforcement (Instructor + Pydantic strict).

LLM MOCKATO (client iniettato duck-typed): nessuna rete. Copre:
  - il wiring: response_model=list[RigaGrezza], max_retries, model, messaggi;
  - fail-closed: un errore a retry esauriti viene PROPAGATO, non inghiottito;
  - la barriera del response_model: `list[RigaGrezza]` rifiuta batch malformati
    (strict via extra=forbid / campi obbligatori) — la stessa validazione che
    Instructor applica sull'output del modello.
"""

from __future__ import annotations

import instructor
import pytest
from pydantic import TypeAdapter, ValidationError

from src.ingestion.extraction.enforce import build_client, enforce_page
from src.ingestion.extraction.schema import RigaGrezza


# ----------------------------------------------------- LLM mockato (duck-typed)


class _FakeCompletions:
    def __init__(self, outcome):
        self._outcome = outcome
        self.last_kwargs: dict | None = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    """Imita la superficie client.chat.completions.create di Instructor."""

    def __init__(self, outcome):
        self.chat = _FakeChat(_FakeCompletions(outcome))


# ----------------------------------------------------------- wiring / happy path


class TestEnforceWiring:
    def test_ritorna_le_righe_validate(self):
        rows = [RigaGrezza(codice_originale="V0-123", prezzo="12.50"),
                RigaGrezza(codice_originale="AB-9000")]
        client = _FakeClient(rows)

        out = enforce_page(client, "…testo listino…", model="glm-ocr", max_retries=3)

        assert out is rows
        kw = client.chat.completions.last_kwargs
        assert kw["response_model"] == list[RigaGrezza]   # lo schema forzato
        assert kw["max_retries"] == 3
        assert kw["model"] == "glm-ocr"

    def test_passa_system_e_user_message(self):
        client = _FakeClient([])
        enforce_page(client, "PAGINA GREZZA", model="m")
        msgs = client.chat.completions.last_kwargs["messages"]
        assert msgs[0]["role"] == "system" and "non inventare" in msgs[0]["content"]
        assert msgs[1] == {"role": "user", "content": "PAGINA GREZZA"}


# --------------------------------------------------------------- fail-closed


class TestFailClosed:
    def test_propaga_errore_a_retry_esauriti(self):
        # Instructor, esauriti i retry su output non-valido, solleva: enforce NON
        # deve inghiottire l'errore ne' restituire una lista vuota "silenziosa".
        boom = RuntimeError("retry esauriti: output non conforme allo schema")
        client = _FakeClient(boom)
        with pytest.raises(RuntimeError, match="retry esauriti"):
            enforce_page(client, "…", model="m")


# ------------------------------------------- barriera del response_model (strict)


class TestResponseModelBarriera:
    """La validazione che `response_model=list[RigaGrezza]` impone sull'output."""

    _adapter = TypeAdapter(list[RigaGrezza])

    def test_batch_valido_passa(self):
        rows = self._adapter.validate_python(
            [{"codice_originale": "V0-123", "prezzo": "12.50"},
             {"codice_originale": "AB-1", "descrizione": "Dado"}])
        assert [r.codice_originale for r in rows] == ["V0-123", "AB-1"]

    def test_campo_inventato_nel_batch_rifiutato(self):
        with pytest.raises(ValidationError):
            self._adapter.validate_python([{"codice_originale": "V0-123", "sconto": 0.2}])

    def test_riga_senza_codice_rifiutata(self):
        with pytest.raises(ValidationError):
            self._adapter.validate_python([{"descrizione": "senza codice"}])

    def test_prezzo_negativo_rifiutato(self):
        with pytest.raises(ValidationError):
            self._adapter.validate_python([{"codice_originale": "V0-1", "prezzo": "-5"}])


# ---------------------------------------------------------------- build_client


class TestBuildClient:
    def test_costruisce_client_instructor(self):
        # nessuna rete: costruzione soltanto (api_key fittizia, endpoint locale)
        client = build_client(base_url="http://localhost:1234/v1", api_key="local-not-needed")
        assert isinstance(client, instructor.Instructor)
        assert hasattr(client.chat.completions, "create")
