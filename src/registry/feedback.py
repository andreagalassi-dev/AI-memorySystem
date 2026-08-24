"""P4.x — Salvataggio feedback e memoria (epica F).

Fornisce la base dati per la memoria a 3 livelli: personale,
di categoria e aziendale.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import psycopg

SCHEMA_PATH = Path(__file__).with_name("feedback_schema.sql")


def init_schema(conn: psycopg.Connection) -> None:
    """Applica il DDL (idempotente: IF NOT EXISTS ovunque)."""
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def insert_feedback(
    conn: psycopg.Connection,
    *,
    utente: str,
    ruolo: str,
    query: str,
    risposta: str,
    final_level: str | None,
    citazioni: list[dict[str, Any]] | None,
    contesto: dict[str, Any] | None,
    verdetto: str,
    correzione: str | None,
    provenienza: str = "corpus"
) -> int:
    """Inserisce un nuovo record di feedback nel database.

    `provenienza` (Epica H, colonna con default 'corpus' per compatibilita'
    con le righe/chiamate esistenti): 'corpus' | 'web' | 'misto' | 'nessuna'.
    Deve stare nel RECORD e non solo nella grafica dell'interfaccia, perche'
    la promozione feedback->memoria (F.2b) legge da qui: se si perdesse a
    schermo, una risposta presa da internet potrebbe salire in memoria
    aziendale senza che nessuno sappia piu' da dove viene.

    Ritorna l'id assegnato.
    """
    row = conn.execute(
        """
        INSERT INTO feedback (
            utente, ruolo, query, risposta, final_level,
            citazioni, contesto, verdetto, correzione, provenienza
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (
            utente,
            ruolo,
            query,
            risposta,
            final_level,
            json.dumps(citazioni) if citazioni is not None else None,
            json.dumps(contesto) if contesto is not None else None,
            verdetto,
            correzione,
            provenienza,
        )
    ).fetchone()
    conn.commit()
    return row[0]
