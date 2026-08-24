"""Debito §2 — Seed del dizionario sinonimi: config/synonyms.json -> Postgres.

IDEMPOTENTE (contratto seed_ingestion_profiles.py): stato gia' allineato ->
0 scritture. I termini `llm_proposed` non approvati sopravvivono al re-seed.

RICONCILIAZIONE (23/08). Fino a ieri il seed era solo ADDITIVO: i concetti
presenti a DB e assenti dal file restavano attivi per sempre. Il risultato
misurato era che lo stato del dizionario NON era ricostruibile dal repo — il
file aveva 41 concetti, la produzione 43 (`nero` e `zincato`, aggiunti a mano
il 02/08 e mai tornati nel seed). Un dizionario che nessuno puo' rileggere dal
repo e' un dizionario di cui nessuno puo' rispondere.

Ora il file e' la FONTE DI VERITA': i concetti attivi a DB e assenti dal file
vengono DISATTIVATI (`is_active = false`), cosi' che dopo il seed valga
`load_synonyms(conn) == load_seed_config(SEED_PATH)`. La disattivazione e'
soft — le righe e i termini restano, quindi il recupero e' un UPDATE, e le
proposte `llm_proposed` in attesa di moderazione non vengono distrutte.

E' un default distruttivo, quindi e' RUMOROSO: ogni slug disattivato viene
stampato. `--keep-extra` ripristina il vecchio comportamento additivo (utile
per curare un concetto a DB prima di riportarlo nel file), e in quel caso il
seed lo dichiara: lo stato risultante non e' ricostruibile dal repo.

Uso: uv run scripts/seed_synonyms.py [--keep-extra]
"""

from __future__ import annotations

import argparse
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


def deactivate_extra(conn, slugs: list[str]) -> None:
    """Disattiva (soft) i concetti attivi non presenti nel seed."""
    conn.execute(
        "UPDATE synonym_concepts SET is_active = false, updated_at = now() "
        "WHERE slug = ANY(%s) AND is_active",
        (slugs,),
    )
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--keep-extra", action="store_true",
                    help="non disattivare i concetti a DB assenti dal file "
                         "(comportamento additivo pre-23/08)")
    args = ap.parse_args(argv)

    cfg = load_seed_config(SEED_PATH)  # fail-fast su JSON/lingue/termini rotti
    n_terms = sum(len(c["terms"]) for c in cfg.values())
    with get_pg_conn() as conn:
        init_schema(conn)
        current = load_synonyms(conn)
        extra = sorted(set(current) - set(cfg))

        # La dichiarazione va PRIMA di ogni uscita: un dizionario divergente
        # deve saperlo anche il run che non scrive niente. E' proprio il
        # silenzio del ramo "gia' allineato" ad aver coperto la deriva.
        if extra and args.keep_extra:
            print(f"[seed-syn] --keep-extra: {len(extra)} concetti restano "
                  f"attivi fuori dal seed ({', '.join(extra)}) — lo stato "
                  "del dizionario NON e' ricostruibile dal repo")

        allineato = (current == cfg if not args.keep_extra
                     else {s: current[s] for s in cfg if s in current} == cfg)
        if allineato:
            print(f"[seed-syn] gia' allineato: {len(cfg)} concetti, "
                  f"{n_terms} termini — 0 scritture")
            return 0

        for slug, concept in cfg.items():
            upsert_concept(conn, slug, concept)

        if extra and not args.keep_extra:
            for slug in extra:
                print(f"[seed-syn] DISATTIVO concetto assente dal seed: {slug}")
            deactivate_extra(conn, extra)

        dopo = load_synonyms(conn)
        ok = ({s: dopo[s] for s in cfg if s in dopo} == cfg if args.keep_extra
              else dopo == cfg)
        if not ok:
            print("[seed-syn] ERRORE: stato a DB dopo il seed != seed")
            return 1
        print(f"[seed-syn] OK: {len(cfg)} concetti, {n_terms} termini "
              f"(concetti attivi a DB: {len(dopo)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
