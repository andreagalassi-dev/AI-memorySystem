"""Test per il salvataggio dei feedback utente (epica F).

Verifica lo schema idempotente e la scrittura base (round-trip) senza logica.
"""

from __future__ import annotations

import pytest
import psycopg

from src.registry.feedback import init_schema, insert_feedback
from tests.conftest import needs_pg


@needs_pg
class TestFeedbackSchema:
    def test_init_schema_idempotente(self, conn: psycopg.Connection):
        init_schema(conn)
        init_schema(conn)  # Second execution should not fail

    def test_insert_round_trip(self, conn: psycopg.Connection):
        init_schema(conn)
        res_id = insert_feedback(
            conn,
            utente="user@example.com",
            ruolo="tecnico",
            query="come funziona X?",
            risposta="funziona cosi",
            final_level="personale",
            citazioni=[{"doc": "doc1", "page": 1}],
            contesto={"evidence": "testo"},
            verdetto="conferma",
            correzione=None,
        )
        assert res_id > 0
        
        row = conn.execute("SELECT utente, ruolo, query, verdetto FROM feedback WHERE id = %s", (res_id,)).fetchone()
        assert row == ("user@example.com", "tecnico", "come funziona X?", "conferma")

    def test_verdetto_enum_constraint(self, conn: psycopg.Connection):
        init_schema(conn)
        with pytest.raises(psycopg.errors.CheckViolation):
            insert_feedback(
                conn,
                utente="user@example.com",
                ruolo="tecnico",
                query="Q",
                risposta="R",
                final_level="personale",
                citazioni=[],
                contesto={},
                verdetto="sbagliato",  # invalid
                correzione=None,
            )
        conn.rollback()

    def test_correzione_null_with_conferma(self, conn: psycopg.Connection):
        init_schema(conn)
        res_id = insert_feedback(
            conn,
            utente="user@example.com",
            ruolo="tecnico",
            query="Q",
            risposta="R",
            final_level=None,
            citazioni=[],
            contesto={},
            verdetto="conferma",
            correzione=None,
        )
        assert res_id > 0
