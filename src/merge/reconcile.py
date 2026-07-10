"""Fase 2.1 — Orchestratore di riconciliazione a corpus (Catalogo x Listino).

Esegue la cucitura dell'Ipotesi B sull'intero `righe_listino`, **un documento alla
volta** (anti-OOM: mai tutto il corpus in RAM). Per ogni `doc_stem`:
  1) materializza le righe del doc (`PgRigheListino.iter_righe(doc_stem=...)`),
  2) `merge()` contro il catalogo visivo (RBAC-aware),
  3) `replace_scarti_for_doc`: delete-by-doc + insert atomici -> idempotenza per doc,
     commit per `doc_stem` (un crash a meta' corpus lascia coerenti i doc gia' fatti).

Le associazioni NON si persistono (Ipotesi B: derivabili da righe_listino + Qdrant,
SSOT Postgres); il report ne riporta solo il conteggio.

CLI di back-office: `python -m src.merge.reconcile --role amministrazione`. Il ruolo
e' OBBLIGATORIO ed esplicito (mai default silenzioso): per la riconciliazione si usa
`amministrazione` (vede tutte le pagine) cosi' un orfano e' una vera assenza-dato e
non un mascheramento RBAC.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

from src.common.roles import Role
from src.merge.relational import VisualCatalog, merge
from src.merge.store import (
    DEFAULT_SCHEMA,
    PgRigheListino,
    get_distinct_doc_stems,
    replace_scarti_for_doc,
)


@dataclass(frozen=True)
class ReconcileReport:
    """Riepilogo della riconciliazione a corpus (per il CLI / l'audit)."""

    docs: int
    associazioni: int
    scarti: int


def reconcile_corpus(conn: psycopg.Connection, catalog: VisualCatalog, *, role: Role,
                     schema: str = DEFAULT_SCHEMA,
                     dry_run: bool = False) -> ReconcileReport:
    """Riconcilia l'intero listino documento-per-documento. Idempotente (re-run non
    duplica), commit per `doc_stem`. Ritorna i conteggi aggregati.

    `dry_run=True`: **read-only** — calcola il report (documenti, associazioni virtuali,
    orfani) senza scrivere nulla in `scarti_merge`. Utile per stimare l'ordine di
    grandezza prima della scrittura definitiva."""
    doc_stems = get_distinct_doc_stems(conn, schema=schema)
    tot_assoc = tot_scarti = 0
    for doc_stem in doc_stems:
        reader = PgRigheListino(conn, schema=schema)
        rows = list(reader.iter_righe(doc_stem=doc_stem))   # chiude il cursore prima di scrivere
        res = merge(rows, catalog, role=role)
        if not dry_run:
            replace_scarti_for_doc(conn, doc_stem, res.scarti, schema=schema)
        tot_assoc += len(res.associazioni)
        tot_scarti += len(res.scarti)
    return ReconcileReport(docs=len(doc_stems), associazioni=tot_assoc, scarti=tot_scarti)


def main(argv: list[str] | None = None) -> None:
    """CLI frame sottile: cabla conninfo + QdrantVisualCatalog reali ed esegue."""
    import argparse

    from src.backend.clients.postgres_client import conninfo
    from src.backend.clients.qdrant_client import get_qdrant_client
    from src.common.roles import parse_role
    from src.merge.relational import QdrantVisualCatalog
    from src.merge.store import init_schema

    ap = argparse.ArgumentParser(
        description="Riconciliazione Catalogo x Listino (Fase 2.1).")
    ap.add_argument("--role", required=True,
                    help="ruolo RBAC (back-office: 'amministrazione' -> vede tutte le pagine)")
    ap.add_argument("--schema", default=DEFAULT_SCHEMA, help="schema Postgres (default: estrazione)")
    ap.add_argument("--dry-run", action="store_true",
                    help="read-only: stampa il report senza scrivere in scarti_merge")
    args = ap.parse_args(argv)

    role = parse_role(args.role)                     # fail-closed su ruolo ignoto
    catalog = QdrantVisualCatalog(get_qdrant_client())
    with psycopg.connect(conninfo()) as conn:
        if not args.dry_run:
            init_schema(conn, schema=args.schema)    # garantisce scarti_merge nello schema target
        rep = reconcile_corpus(conn, catalog, role=role, schema=args.schema,
                               dry_run=args.dry_run)
    suffix = "  (dry-run: nessuna scrittura)" if args.dry_run else ""
    print(f"riconciliazione: doc={rep.docs} associazioni={rep.associazioni} "
          f"scarti={rep.scarti}{suffix}")


if __name__ == "__main__":
    main()
