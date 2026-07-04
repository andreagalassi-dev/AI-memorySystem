"""Debito §2 — Seed del dizionario sinonimi: config/synonyms.json -> Postgres.

IDEMPOTENTE (contratto seed_ingestion_profiles.py): stato gia' allineato ->
0 scritture. I concetti extra a DB (aggiunti a mano o dal futuro estrattore
LLM) NON vengono rimossi dal re-seed; i termini `llm_proposed` sopravvivono.

Uso: uv run scripts/seed_synonyms.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.postgres_client import get_pg_conn  # noqa: E402
from src.registry.synonyms import (  # noqa: E402
    init_schema,
    load_seed_config,
    load_synonyms,
    upsert_concept,
)

SEED_PATH = ROOT / "config" / "synonyms.json"


def main() -> int:
    cfg = load_seed_config(SEED_PATH)  # fail-fast su JSON/lingue/termini rotti
    n_terms = sum(len(c["terms"]) for c in cfg.values())
    with get_pg_conn() as conn:
        init_schema(conn)
        current = load_synonyms(conn)
        if {s: current[s] for s in cfg if s in current} == cfg:
            print(f"[seed-syn] gia' allineato: {len(cfg)} concetti, "
                  f"{n_terms} termini — 0 scritture")
            return 0

        for slug, concept in cfg.items():
            upsert_concept(conn, slug, concept)

        dopo = load_synonyms(conn)
        if {s: dopo[s] for s in cfg if s in dopo} != cfg:
            print("[seed-syn] ERRORE: stato a DB dopo il seed != seed")
            return 1
        print(f"[seed-syn] OK: {len(cfg)} concetti, {n_terms} termini "
              f"(concetti totali a DB: {len(dopo)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
