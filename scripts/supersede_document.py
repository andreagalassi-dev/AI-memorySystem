"""P4.3 — Flusso "nuova versione": switch SSOT-first + riconciliazione Qdrant.

Fase 1 (atomica, Postgres): la versione corrente dello slug diventa
`superseded`, il nuovo doc_stem diventa `current` (embedding MAI cancellati).
Fase 2 (convergente, Qdrant): riconciliazione dallo stato SSOT su entrambe
le collection. RILANCIABILE ALLA CIECA: se Qdrant va in timeout a meta'
patch, rieseguire LO STESSO comando completa lo switch senza creare v3
(guardia di idempotenza) e senza ri-patchare cio' che e' gia' allineato.

Prerequisito: i punti del nuovo doc_stem dovrebbero essere gia' ingeriti
(pipeline P2); se mancano, lo switch avviene comunque nella SSOT e lo
script lo segnala: ingerire e rilanciare la sync.

Uso: uv run scripts/supersede_document.py <slug> <new_doc_stem> --langs it,en
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.postgres_client import get_pg_conn  # noqa: E402
from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.ingestion.embedding import COLLECTION  # noqa: E402
from src.registry.registry import supersede_document  # noqa: E402
from src.registry.sync import sync_collection  # noqa: E402
from src.retrieval.qwen3vl import VISUAL_COLLECTION  # noqa: E402

COLLECTIONS = (COLLECTION, VISUAL_COLLECTION)


def main() -> None:
    ap = argparse.ArgumentParser(description="Switch di versione (SPEC §E)")
    ap.add_argument("slug", help="documento logico nel registro")
    ap.add_argument("new_doc_stem", help="doc stem della nuova versione")
    ap.add_argument("--langs", default="", help="lingue separate da virgola")
    args = ap.parse_args()
    langs = [x.strip() for x in args.langs.split(",") if x.strip()]

    qc = get_qdrant_client()

    def sync(export):
        return [sync_collection(qc, coll, export) for coll in COLLECTIONS]

    try:
        conn = get_pg_conn()
        rep = supersede_document(conn, slug=args.slug,
                                 new_doc_stem=args.new_doc_stem,
                                 langs=langs, sync=sync)
        conn.close()
    except ValueError as exc:
        print(f"RIFIUTATO: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"ERRORE ({type(exc).__name__}): {exc}\n"
              "La SSOT potrebbe essere gia' aggiornata: rilanciare LO STESSO "
              "comando — e' idempotente e riprende dalla riconciliazione.")
        sys.exit(1)

    print(f"[fase 1] Postgres: {rep.phase1}")
    for r in rep.sync_reports:
        print(f"[fase 2] {r.collection}: {len(r.patched)} patchati "
              f"({', '.join(r.patched) or '-'}), {r.skipped} gia' allineati")
    for w in rep.warnings:
        print(f"ATTENZIONE: {w}")
    print("SWITCH COMPLETATO" + (" (con avvisi)" if rep.warnings else ""))


if __name__ == "__main__":
    main()
