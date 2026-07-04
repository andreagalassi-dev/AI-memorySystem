"""Fixture condivise (P4): Postgres del compose, schema temporaneo usa-e-getta."""

from __future__ import annotations

import pytest


def _pg_available() -> bool:
    try:
        import psycopg

        from src.backend.clients.postgres_client import conninfo
        with psycopg.connect(conninfo(), connect_timeout=2):
            return True
    except Exception:
        return False


needs_pg = pytest.mark.skipif(
    not _pg_available(),
    reason="richiede il Postgres del compose (docker compose up -d postgres)",
)


@pytest.fixture()
def conn():
    """Connessione su schema temporaneo: il registro reale non viene toccato."""
    import psycopg

    from src.backend.clients.postgres_client import conninfo
    with psycopg.connect(conninfo()) as c:
        c.execute("DROP SCHEMA IF EXISTS test_registry CASCADE")
        c.execute("CREATE SCHEMA test_registry")
        c.execute("SET search_path TO test_registry")
        c.commit()  # senza commit il rollback di teardown annullerebbe lo schema
        yield c
        c.rollback()
        c.execute("DROP SCHEMA IF EXISTS test_registry CASCADE")
        c.commit()
