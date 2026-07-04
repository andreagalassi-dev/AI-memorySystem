"""P4.1 — Popolamento iniziale del registro versioni su PostgreSQL (SSOT).

Legge da Qdrant in SOLA LETTURA i doc stems esistenti in `aike_chunks`
(payload doc/cat/lang) e li registra su Postgres come v1/current con i
ruoli default per categoria (config/default_roles.json). Per i documenti
esistenti slug logico = doc stem (prima versione nota). IDEMPOTENTE:
una seconda esecuzione non modifica nulla (0 creati).

Prerequisiti: docker compose up -d postgres qdrant; uv sync.

Sequenza fail-fast (exit 1 al primo check rosso):
  [1/5] Postgres raggiungibile + schema applicato (idempotente)
  [2/5] scroll aike_chunks -> doc stems distinti (attesi 57)
  [3/5] mapping ruoli default caricato e valido (_fallback presente)
  [4/5] upsert nel registro: v1/current + ruoli per categoria
  [5/5] verifiche: n documenti == n stems, tutti current, ruoli coerenti

Uso: uv run scripts/registry_populate.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.postgres_client import get_pg_conn  # noqa: E402
from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.registry.registry import init_schema, list_documents, register_document  # noqa: E402
from src.registry.seed import (  # noqa: E402
    aggregate_docs,
    load_default_roles,
    roles_for_category,
)

COLLECTION = "aike_chunks"


def die(msg: str) -> None:
    print(f"  ERRORE: {msg}")
    sys.exit(1)


def scroll_payloads(qc) -> list[dict]:
    out: list[dict] = []
    offset = None
    while True:
        points, offset = qc.scroll(
            COLLECTION, limit=1000, offset=offset,
            with_payload=["doc", "cat", "lang"], with_vectors=False)
        out.extend(p.payload for p in points)
        if offset is None:
            return out


def main() -> None:
    print("[1/5] Postgres + schema (idempotente)")
    try:
        conn = get_pg_conn()
        init_schema(conn)
    except Exception as exc:
        die(f"Postgres non disponibile: {exc}")
    print("  ok")

    print(f"[2/5] scroll {COLLECTION} (read-only)")
    try:
        payloads = scroll_payloads(get_qdrant_client())
    except Exception as exc:
        die(f"Qdrant non disponibile: {exc}")
    docs = aggregate_docs(payloads)
    if not docs:
        die("nessun doc stem trovato: collezione vuota?")
    print(f"  {len(payloads)} punti -> {len(docs)} doc stems distinti")

    print("[3/5] mapping ruoli default")
    mapping = load_default_roles()
    print(f"  {len(mapping) - 1} categorie + _fallback")

    print("[4/5] upsert registro (v1/current + ruoli)")
    created = skipped = 0
    for stem in sorted(docs):
        seed = docs[stem]
        res = register_document(
            conn, slug=stem, category=seed.category,
            roles=roles_for_category(seed.category, mapping),
            doc_stem=stem, langs=seed.langs)
        created += res.created
        skipped += not res.created
    print(f"  creati {created}, gia' presenti {skipped}")

    print("[5/5] verifiche finali")
    regs = list_documents(conn)
    if len(regs) != len(docs):
        die(f"registro ha {len(regs)} documenti, attesi {len(docs)}")
    missing_current = [d.slug for d in regs if d.current_stem is None]
    if missing_current:
        die(f"documenti senza versione current: {missing_current[:5]}")
    n_current, = conn.execute(
        "SELECT count(*) FROM document_versions WHERE version_status='current'").fetchone()
    n_sup, = conn.execute(
        "SELECT count(*) FROM document_versions WHERE version_status='superseded'").fetchone()
    no_role, = conn.execute(
        "SELECT count(*) FROM documents d WHERE NOT EXISTS "
        "(SELECT 1 FROM document_roles r WHERE r.document_id = d.id)").fetchone()
    no_tec, = conn.execute(
        "SELECT count(*) FROM documents d WHERE NOT EXISTS "
        "(SELECT 1 FROM document_roles r WHERE r.document_id = d.id "
        " AND r.role = 'tecnico')").fetchone()
    if no_role or no_tec:
        die(f"ruoli incoerenti: {no_role} doc senza ruoli, {no_tec} senza tecnico")
    per_cat = Counter(d.category for d in regs)
    per_role = dict(conn.execute(
        "SELECT role, count(*) FROM document_roles GROUP BY role ORDER BY role").fetchall())
    print(f"  documenti {len(regs)} | current {n_current} | superseded {n_sup}")
    print(f"  per categoria: {dict(sorted(per_cat.items(), key=lambda kv: str(kv[0])))}")
    print(f"  per ruolo: {per_role}")
    conn.close()
    print("REGISTRO POPOLATO: verifiche ok")


if __name__ == "__main__":
    main()
