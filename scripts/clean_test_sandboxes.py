#!/usr/bin/env python3
"""Pulizia dei residui di sandbox lasciati da test @pytest.mark.live interrotti.

Contesto: `tests/test_visual_collection_param.py` crea, per ogni run del test
live, uno schema Postgres `c_vis_<hash8>` e due collezioni Qdrant
`chunks_<hash8>` / `visual_<hash8>` (stesso hash). Se il processo viene
interrotto duramente (kill -9, TaskStop del harness, crash) nessun codice
Python di teardown puo' girare — ne' un `finally`, ne' il teardown di una
fixture pytest — e questi residui restano nel DB/Qdrant per sempre.

Questo script li ELENCA sempre; li CANCELLA solo se lanciato con `--apply`.
Di default e' `--dry-run` (sola lettura, nessuna modifica).

Sicurezza (voluta ridondante): un nome e' considerato "sandbox" SOLO SE
supera DUE controlli indipendenti:
  1. non e' nella lista nera esplicita dei nomi di produzione;
  2. rispetta il pattern esatto della sandbox (prefisso + 8 caratteri hex).
Se anche uno solo dei due controlli fallisce, il nome viene SCARTATO. Questo
e' pensato per resistere anche a un refuso nel pattern: la lista nera copre
i nomi di produzione conosciuti indipendentemente dalla regex.

Uso:
    python scripts/clean_test_sandboxes.py                # dry-run (default)
    python scripts/clean_test_sandboxes.py --dry-run       # esplicito, uguale
    python scripts/clean_test_sandboxes.py --apply         # cancella davvero
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import psycopg
from psycopg import sql

from src.backend.clients.postgres_client import conninfo
from src.backend.clients.qdrant_client import get_qdrant_client

# --- Pattern della sandbox: prefisso fisso + 8 caratteri hex (uuid4().hex[:8]) ---
PG_SCHEMA_PATTERN = re.compile(r"^c_vis_[0-9a-f]{8}$")
QDRANT_CHUNKS_PATTERN = re.compile(r"^chunks_[0-9a-f]{8}$")
QDRANT_VISUAL_PATTERN = re.compile(r"^visual_[0-9a-f]{8}$")

# --- Lista nera esplicita: nomi di produzione che non devono MAI essere
# cancellabili da questo strumento, a prescindere dal pattern sopra. ---
PG_SCHEMA_BLACKLIST = {
    "public",
    "estrazione",
    "chainlit",
    "information_schema",
    "pg_catalog",
    "pg_toast",
}
QDRANT_COLLECTION_BLACKLIST = {
    "aike_chunks",
    "aike_chunks_v2",
    "aike_chunks_v2en",
    "aike_pages_visual",
    "aike_pages_visual_colqwen3",
}


def is_sandbox_pg_schema(name: str) -> bool:
    """True solo se `name` NON e' in lista nera E rispetta il pattern sandbox."""
    if name in PG_SCHEMA_BLACKLIST:
        return False
    return bool(PG_SCHEMA_PATTERN.match(name))


def is_sandbox_qdrant_collection(name: str) -> bool:
    """True solo se `name` NON e' in lista nera E rispetta un pattern sandbox
    (chunks_<hex8> o visual_<hex8>)."""
    if name in QDRANT_COLLECTION_BLACKLIST:
        return False
    return bool(QDRANT_CHUNKS_PATTERN.match(name) or QDRANT_VISUAL_PATTERN.match(name))


def find_pg_sandbox_schemas(conn: psycopg.Connection) -> list[str]:
    """Elenca (sola lettura) gli schemi Postgres che matchano il pattern sandbox."""
    rows = conn.execute(
        "SELECT schema_name FROM information_schema.schemata ORDER BY schema_name"
    ).fetchall()
    return [r[0] for r in rows if is_sandbox_pg_schema(r[0])]


def find_qdrant_sandbox_collections(client) -> list[str]:
    """Elenca (sola lettura) le collezioni Qdrant che matchano il pattern sandbox."""
    cols = client.get_collections().collections
    return sorted(c.name for c in cols if is_sandbox_qdrant_collection(c.name))


def delete_pg_schema(conn: psycopg.Connection, schema: str) -> None:
    assert is_sandbox_pg_schema(schema), f"rifiuto di cancellare uno schema non-sandbox: {schema!r}"
    conn.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
    conn.commit()


def delete_qdrant_collection(client, name: str) -> None:
    assert is_sandbox_qdrant_collection(name), f"rifiuto di cancellare una collezione non-sandbox: {name!r}"
    if client.collection_exists(name):
        client.delete_collection(name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="Cancella davvero i residui trovati (default: dry-run, sola lettura).")
    parser.add_argument("--dry-run", action="store_true", help="Esplicito no-op: elenca soltanto (comportamento di default).")
    args = parser.parse_args(argv)

    apply = args.apply and not args.dry_run  # --dry-run vince sempre, anche insieme ad --apply

    with psycopg.connect(conninfo()) as conn:
        pg_schemas = find_pg_sandbox_schemas(conn)

    qc = get_qdrant_client()
    qdrant_collections = find_qdrant_sandbox_collections(qc)

    print(f"Schemi Postgres sandbox trovati ({len(pg_schemas)}):")
    for s in pg_schemas:
        print(f"  - {s}")
    print(f"Collezioni Qdrant sandbox trovate ({len(qdrant_collections)}):")
    for c in qdrant_collections:
        print(f"  - {c}")

    total = len(pg_schemas) + len(qdrant_collections)
    if total == 0:
        print("Nessun residuo trovato.")
        return 0

    if not apply:
        print(f"\nDRY-RUN: nessuna modifica. {total} residui verrebbero cancellati con --apply.")
        return 0

    print(f"\n--apply: cancellazione di {total} residui in corso...")
    errors = []
    with psycopg.connect(conninfo()) as conn:
        for s in pg_schemas:
            try:
                delete_pg_schema(conn, s)
                print(f"  cancellato schema {s}")
            except Exception as exc:  # continua sugli altri anche se uno fallisce
                errors.append((s, exc))
                print(f"  ERRORE su schema {s}: {exc}")

    for c in qdrant_collections:
        try:
            delete_qdrant_collection(qc, c)
            print(f"  cancellata collezione {c}")
        except Exception as exc:
            errors.append((c, exc))
            print(f"  ERRORE su collezione {c}: {exc}")

    if errors:
        print(f"\n{len(errors)} errori durante la cancellazione.")
        return 1
    print("\nFatto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
