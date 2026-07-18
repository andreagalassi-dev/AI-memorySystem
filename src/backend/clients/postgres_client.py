"""Factory della connessione PostgreSQL (registro versioni P4.1).

Come qdrant_client: costruzione lazy, errori di raggiungibilita' gestiti
dal chiamante. Nessun pool per la Fase 0 (script batch e test).
"""

from __future__ import annotations

import psycopg

from src.backend.config import settings


def conninfo() -> str:
    """Stringa di connessione da env (mai loggarla: contiene la password)."""
    return (
        f"host={settings.postgres_host} port={settings.postgres_port} "
        f"user={settings.postgres_user} password={settings.postgres_password} "
        f"dbname={settings.postgres_db}"
    )


def get_pg_conn() -> psycopg.Connection:
    """Apre una NUOVA connessione (autocommit off: il chiamante fa commit)."""
    return psycopg.connect(conninfo())


def conninfo_readonly() -> str:
    """Come `conninfo()` ma col ruolo SELECT-only (Fase 1.3 ramo STRUCTURED).

    Difesa in profondita': la connessione del tool SQL non puo' scrivere, il DB
    stesso rifiuta INSERT/UPDATE/DELETE. Il ruolo nasce con l'init-db del compose
    (docker/init-db) o va creato una volta a mano su un volume gia' popolato."""
    return (
        f"host={settings.postgres_host} port={settings.postgres_port} "
        f"user={settings.postgres_ro_user} password={settings.postgres_ro_password} "
        f"dbname={settings.postgres_db}"
    )


def get_pg_conn_readonly() -> psycopg.Connection:
    """Apre una NUOVA connessione col ruolo read-only (sola lettura)."""
    return psycopg.connect(conninfo_readonly())
