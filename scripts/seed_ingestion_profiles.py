"""Debito §1 — Seed/migrazione dei Profili di Ingestion: brands.json -> Postgres.

Applica il DDL (idempotente) e allinea le tabelle al seed config/brands.json.
IDEMPOTENTE: se lo stato a DB coincide gia' col seed, 0 scritture (stesso
contratto di registry_populate.py). Ogni regex e' validata PRIMA dell'INSERT.
Dopo la scrittura verifica il round-trip: load_profiles(DB) == seed canonico.

Uso: uv run scripts/seed_ingestion_profiles.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.postgres_client import get_pg_conn  # noqa: E402
from src.registry.ingestion_profiles import (  # noqa: E402
    init_schema,
    load_profiles,
    load_seed_config,
    set_doc_map,
    upsert_profile,
)

SEED_PATH = ROOT / "config" / "brands.json"


def main() -> int:
    cfg = load_seed_config(SEED_PATH)  # fail-fast su regex/JSON rotti
    with get_pg_conn() as conn:
        init_schema(conn)
        current = load_profiles(conn)
        if current == cfg:
            print(f"[seed] gia' allineato: {len(cfg['profiles'])} profili, "
                  f"{len(cfg['doc_map'])} glob doc_map — 0 scritture")
            return 0

        for name, profile in cfg["profiles"].items():
            upsert_profile(conn, name, profile)
        set_doc_map(conn, cfg["doc_map"])

        dopo = load_profiles(conn)
        if dopo != cfg:  # round-trip rotto: mai fallire in silenzio
            print("[seed] ERRORE: lo stato a DB dopo il seed NON coincide col seed")
            return 1
        print(f"[seed] OK: {len(cfg['profiles'])} profili "
              f"({', '.join(cfg['profiles'])}), {len(cfg['doc_map'])} glob doc_map")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
