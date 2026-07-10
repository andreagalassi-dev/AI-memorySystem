"""P4.6 — Verifica FORMALE del DoD di P4 sul sistema reale (end-to-end).

DoD P4 (SPEC riga 104): (1) caricare una nuova versione marca la vecchia
come sostituita; (2) l'avviso "obsoleto" compare; (3) un ruolo non vede i
documenti di un altro ruolo.

La demo esegue uno switch di versione REALE su un documento bersaglio
(tecnico-only, con un codice esclusivo) e poi RIPRISTINA lo stato:
correzione della SSOT + riconciliazione convergente (P4.3) — il ripristino
stesso e' un'ulteriore prova del design. Prerequisiti: postgres, qdrant e
Ollama su. Se lo script muore a meta', rilanciare: la coda di ripristino
e' idempotente (SSOT-first).

Uso: uv run scripts/verify_p4_dod.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdrant_client import models  # noqa: E402

from src.backend.clients.postgres_client import get_pg_conn  # noqa: E402
from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.common.roles import Role  # noqa: E402
from src.graph.pipeline import answer  # noqa: E402
from src.ingestion.embedding import COLLECTION  # noqa: E402
from src.registry.registry import (  # noqa: E402
    export_backfill,
    get_current_version,
    supersede_document,
)
from src.registry.sync import sync_collection  # noqa: E402
from src.retrieval.synthesis import REFUSAL  # noqa: E402
from src.retrieval.qwen3vl import VISUAL_COLLECTION  # noqa: E402

COLLECTIONS = (COLLECTION, VISUAL_COLLECTION)


def die(msg: str) -> None:
    print(f"  DOD FALLITO: {msg}")
    sys.exit(1)


def count(qc, **conds) -> int:
    flt = models.Filter(must=[
        models.FieldCondition(key=k, match=models.MatchValue(value=v))
        for k, v in conds.items()])
    return qc.count(COLLECTION, exact=True, count_filter=flt).count


def pick_target(qc, export):
    """Doc tecnico-only con un codice ESCLUSIVO (vive solo li')."""
    for stem in sorted(export):
        if export[stem].ruoli != [Role.TECNICO]:
            continue
        pts, _ = qc.scroll(COLLECTION, limit=50, with_payload=["code", "desc"],
                           scroll_filter=models.Filter(must=[
                               models.FieldCondition(
                                   key="doc", match=models.MatchValue(value=stem))]))
        for p in pts:
            code, desc = p.payload.get("code"), p.payload.get("desc")
            # codice "vero" (>=6 char) CON descrizione e con UN SOLO punto in
            # tutto il corpus: righe duplicate dello stesso codice producono
            # margine 0.00 -> "non lo so" (visto con '90716C', design P3.2);
            # un punto unico forza margine 1.0 -> percorso confidente
            if not (code and desc and len(code) >= 6):
                continue
            if count(qc, code=code) == 1 and count(qc, code=code, doc=stem) == 1:
                return stem, code
    die("nessun bersaglio trovato (doc tecnico-only con codice esclusivo)")


def main() -> None:
    qc = get_qdrant_client()
    conn = get_pg_conn()
    export = export_backfill(conn)

    print("[1/6] selezione bersaglio")
    stem, code = pick_target(qc, export)
    n_points = count(qc, doc=stem)
    v2_stem = f"{stem}__v2_demo_p46"
    print(f"  doc={stem} ({n_points} punti, solo tecnico) | codice esclusivo={code}")

    print("[2/6] baseline PRE-switch: tecnico riceve risposta SENZA avviso")
    r = answer(code, "tecnico")
    if r["final_level"] == "BASSA" or "SOSTITUITA" in r["final_answer"]:
        die(f"baseline anomala: {r['final_level']} / {r['final_answer'][:80]}")
    print(f"  {r['final_level']} | {r['final_answer'][:90]}")

    print("[3/6] DoD-1: switch versione (v2 current, v1 superseded, embedding intatti)")
    def sync(exp):
        return [sync_collection(qc, coll, exp) for coll in COLLECTIONS]
    rep = supersede_document(conn, slug=stem, new_doc_stem=v2_stem,
                             langs=["it"], sync=sync)
    cur = get_current_version(conn, stem)
    if cur.doc_stem != v2_stem or cur.version_no != 2:
        die(f"SSOT: current atteso {v2_stem} v2, trovato {cur}")
    if count(qc, doc=stem, version_status="superseded") != n_points:
        die("Qdrant: i punti della v1 non sono tutti superseded")
    if count(qc, doc=stem) != n_points:
        die("Qdrant: embedding cancellati! (violazione SPEC §E)")
    print(f"  fase1={rep.phase1} | SSOT: v2 current | Qdrant: {n_points}/{n_points} "
          f"superseded, 0 cancellati | avvisi: {len(rep.warnings)} (v2 senza punti, atteso)")

    print("[4/6] DoD-2: l'avviso obsoleto COMPARE (cascade fallback e2e)")
    r = answer(code, "tecnico")
    ok = ("SOSTITUITA" in r["final_answer"] and r["final_level"] == "MEDIA"
          and any("fallback" in a.lower() for a in r["audit"]))
    if not ok:
        die(f"avviso assente: {r['final_level']} / {r['final_answer'][:120]} "
            f"/ audit={r['audit']}")
    print(f"  {r['final_level']} | ...{r['final_answer'][-95:]}")

    print("[5/6] DoD-3: il commerciale NON vede il documento tecnico (nemmeno obsoleto)")
    r = answer(code, "commerciale")
    if r["final_level"] != "BASSA" or r["final_answer"] != REFUSAL:
        die(f"LEAK: {r['final_level']} / {r['final_answer'][:80]}")
    print(f"  {r['final_level']} | {r['final_answer']} (zero hit su entrambi gli scope)")

    print("[6/6] ripristino: correzione SSOT + riconciliazione convergente")
    conn.execute("UPDATE document_versions SET superseded_by_version_id=NULL, "
                 "superseded_at=NULL WHERE doc_stem=%s", (stem,))
    conn.execute("DELETE FROM document_versions WHERE doc_stem=%s", (v2_stem,))
    conn.execute("UPDATE document_versions SET version_status='current' "
                 "WHERE doc_stem=%s", (stem,))
    conn.commit()
    reports = sync(export_backfill(conn))
    conn.close()
    if count(qc, doc=stem, version_status="current") != n_points:
        die("ripristino incompleto su Qdrant")
    r = answer(code, "tecnico")
    if "SOSTITUITA" in r["final_answer"] or r["final_level"] == "BASSA":
        die(f"post-ripristino anomalo: {r['final_level']} / {r['final_answer'][:80]}")
    print(f"  sync: {[(x.collection, len(x.patched)) for x in reports]} | "
          f"controllo: {r['final_level']} senza avviso")
    print("DOD P4 VERIFICATO FORMALMENTE: (1) switch ok, (2) avviso obsoleto ok, "
          "(3) segregazione ruoli ok — stato ripristinato")


if __name__ == "__main__":
    main()
