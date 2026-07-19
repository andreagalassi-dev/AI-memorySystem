"""Fase B — B5: prova END-TO-END del ciclo completo del Watchdog.

Dimostra, con codice REALE (nessun mock): aggiungi -> cerca -> modifica -> cerca
-> elimina -> l'avviso "ELIMINATO" compare. Esercita i fix di Fase A (A3 reconcile,
A4 cascata soft-delete) insieme all'auto-ingestion di Fase B (B2/B3/B4).

ISOLAMENTO (sicurezza): il registro di produzione ha gia' 57 documenti e la
collezione `aike_chunks` ~33k punti. Per NON mutarli, questo e2e gira su:
  - uno schema Postgres temporaneo (watchdog_e2e), creato e distrutto qui;
  - una collezione Qdrant dedicata (test_e2e_...), creata e distrutta qui.
Il nome-collezione e' l'unico override: viene puntato alla collezione demo nei tre
moduli che lo leggono (ingest_one, embedding->soft_delete, retrieval.search). Tutto
il resto — estrazione Docling, embedding BGE-M3/BM25, upsert/delete Qdrant,
versioning Postgres, cascata sync_collection, reconcile — e' il codice di produzione.

L'estrazione e' cappata a 2 pagine (Docling reale, solo piu' veloce).

Uso: uv run python scripts/watchdog_e2e.py
Exit: 0 = ciclo dimostrato (tutte le asserzioni verdi); 1 = fallimento.
"""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import psycopg  # noqa: E402

from src.backend.clients.postgres_client import conninfo  # noqa: E402
from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402

# --- moduli di cui sovrascriviamo SOLO il nome-collezione (isolamento) ----------
import src.ingestion.embedding as embedding  # noqa: E402
import src.ingestion.ingest_one as ingest_one  # noqa: E402
import src.retrieval.search as search_mod  # noqa: E402

CORPUS = ROOT / "corpus" / "pumps"
PDF_V1 = CORPUS / "iwaki_TC-X031VTN.pdf"      # versione 1
PDF_V2 = CORPUS / "iwaki_TC-X200SSN.pdf"      # versione 2 (contenuto diverso)
QUERY = "ricambi pompa"
ROLE = "tecnico"
STEM = "demo_ricambi"


def line(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def count_stem(qc, coll: str, status: str | None = None) -> int:
    from qdrant_client import models
    must = [models.FieldCondition(key="doc_stem",
                                  match=models.MatchValue(value=STEM))]
    if status:
        must.append(models.FieldCondition(key="version_status",
                                          match=models.MatchValue(value=status)))
    return qc.count(coll, exact=True, count_filter=models.Filter(must=must)).count


def cite_top(hits) -> str:
    if not hits:
        return "NESSUN HIT"
    p = hits[0].payload
    return (f"doc={p.get('doc')} pagina={p.get('page')} "
            f"codice={p.get('code')} status={p.get('version_status')} "
            f"score={round(hits[0].score, 4)}")


def main() -> int:
    from src.common.roles import parse_role
    from src.registry.registry import init_schema
    from src.registry.ingestion_profiles import init_schema as init_profiles
    from src.retrieval.search import search, access_filter
    from src.watchdog.scanner import scan_directory, diff_files
    from src.watchdog.worker import process_diffs
    from src.graph.pipeline import reconcile

    role = parse_role(ROLE)
    demo_coll = f"test_e2e_{uuid.uuid4().hex[:8]}"
    watch_dir = ROOT / "output" / "watchdog_e2e_inbox"
    if watch_dir.exists():
        shutil.rmtree(watch_dir)
    watch_dir.mkdir(parents=True, exist_ok=True)
    qc = get_qdrant_client()

    # --- override isolante del nome-collezione nei 3 moduli che lo leggono ------
    orig = (embedding.COLLECTION, ingest_one.COLLECTION, search_mod.COLLECTION)
    embedding.COLLECTION = demo_coll      # letto da soft_delete (import interno)
    ingest_one.COLLECTION = demo_coll     # default runtime di ingest_document
    search_mod.COLLECTION = demo_coll     # letto da search()
    # cap estrazione a 2 pagine (Docling reale, solo piu' veloce)
    orig_extract = ingest_one._default_extract
    ingest_one._default_extract = lambda fp, *, limit=0: orig_extract(fp, limit=2)

    conn = psycopg.connect(conninfo())
    try:
        conn.execute("DROP SCHEMA IF EXISTS watchdog_e2e CASCADE")
        conn.execute("CREATE SCHEMA watchdog_e2e")
        conn.execute("SET search_path TO watchdog_e2e")
        conn.commit()
        init_schema(conn)
        init_profiles(conn)

        # ================= 1. AGGIUNGI ==================
        line("1. AGGIUNGI — copio un PDF nella cartella monitorata ed eseguo un ciclo")
        shutil.copy(PDF_V1, watch_dir / "demo_ricambi.pdf")
        diffs = diff_files(conn, scan_directory(str(watch_dir)), str(watch_dir))
        print(f"  diff rilevato: {diffs}")
        process_diffs(conn, diffs, str(watch_dir), default_role=ROLE)
        row = conn.execute(
            "SELECT status, profile_warning, document_id FROM tracked_files "
            "WHERE filename=%s", ("demo_ricambi.pdf",)).fetchone()
        print(f"  tracked_files: status={row[0]} profile_warning={row[1]} "
              f"document_id={row[2]}")
        n_cur = count_stem(qc, demo_coll, "current")
        print(f"  Qdrant: punti current del doc = {n_cur}")
        assert row[0] == "active", f"atteso active, ottenuto {row[0]}"
        assert n_cur > 0, "nessun punto ingerito"

        # ================= 2. CERCA ==================
        line("2. CERCA — interrogo il sistema: deve rispondere citando la fonte")
        hits = search(qc, QUERY, "rrf", limit=5,
                      query_filter=access_filter("current", role))
        print(f"  query={QUERY!r} ruolo={ROLE}")
        print(f"  citazione top-1: {cite_top(hits)}")
        assert hits, "la ricerca non trova il documento appena aggiunto"
        assert hits[0].payload.get("doc"), "risposta senza fonte citabile"

        # ================= 3. MODIFICA ==================
        line("3. MODIFICA — sovrascrivo con un contenuto diverso ed eseguo un ciclo")
        n_before = count_stem(qc, demo_coll)
        shutil.copy(PDF_V2, watch_dir / "demo_ricambi.pdf")
        diffs = diff_files(conn, scan_directory(str(watch_dir)), str(watch_dir))
        print(f"  diff rilevato: {diffs}")
        assert diffs.get("demo_ricambi.pdf") == "modified", "atteso 'modified'"
        process_diffs(conn, diffs, str(watch_dir), default_role=ROLE)
        n_after = count_stem(qc, demo_coll)
        print(f"  punti PRIMA del re-ingest = {n_before}, DOPO = {n_after} "
              f"(niente somma/orfani: B3)")

        # ================= 4. CERCA (post-modifica) ==================
        line("4. CERCA (post-modifica) — la fonte resta citabile, contenuto aggiornato")
        hits = search(qc, QUERY, "rrf", limit=5,
                      query_filter=access_filter("current", role))
        print(f"  citazione top-1: {cite_top(hits)}")
        assert hits, "la ricerca non trova il documento dopo la modifica"

        # ================= 5. ELIMINA ==================
        line("5. ELIMINA — rimuovo il file, eseguo un ciclo: cascata soft-delete (A4)")
        (watch_dir / "demo_ricambi.pdf").unlink()
        diffs = diff_files(conn, scan_directory(str(watch_dir)), str(watch_dir))
        print(f"  diff rilevato: {diffs}")
        assert diffs.get("demo_ricambi.pdf") == "deleted", "atteso 'deleted'"
        process_diffs(conn, diffs, str(watch_dir), default_role=ROLE)
        n_cur = count_stem(qc, demo_coll, "current")
        n_del = count_stem(qc, demo_coll, "deleted")
        print(f"  Qdrant dopo l'eliminazione: current={n_cur} deleted={n_del}")
        assert n_cur == 0, "punti ancora 'current' dopo l'eliminazione (cascata rotta)"
        assert n_del > 0, "SSOT e Qdrant divergono: nessun punto 'deleted'"

        # ================= 6. AVVISO ELIMINATO ==================
        line("6. AVVISO — una risposta basata sul documento eliminato avvisa (A3)")
        # scope 'current' non trova nulla; il fallback cerca nell'eliminato
        cur_hits = search(qc, QUERY, "rrf", limit=5,
                          query_filter=access_filter("current", role))
        del_hits = search(qc, QUERY, "rrf", limit=5,
                          query_filter=access_filter("deleted", role))
        print(f"  hit in 'current' = {len(cur_hits)} | hit in 'deleted' = {len(del_hits)}")
        # reconcile REALE con deleted_warning derivato dall'evidenza eliminata trovata
        out = reconcile({"deleted_warning": bool(del_hits), "level": "BASSA",
                         "answer": ""})
        print(f"  final_level = {out['final_level']}")
        print("  final_answer:\n    " + out["final_answer"].replace("\n", "\n    "))
        assert not cur_hits, "il doc eliminato NON deve comparire nello scope current"
        assert "ELIMINATO" in out["final_answer"], "manca l'avviso ELIMINATO"
        assert out["final_level"] == "BASSA"

        line("ESITO — ciclo completo dimostrato: tutte le asserzioni VERDI")
        return 0
    finally:
        # teardown: nessuna traccia lasciata in produzione
        embedding.COLLECTION, ingest_one.COLLECTION, search_mod.COLLECTION = orig
        ingest_one._default_extract = orig_extract
        try:
            if qc.collection_exists(demo_coll):
                qc.delete_collection(demo_coll)
        except Exception as exc:
            print(f"  (teardown Qdrant: {exc})")
        try:
            conn.execute("DROP SCHEMA IF EXISTS watchdog_e2e CASCADE")
            conn.commit()
        except Exception:
            conn.rollback()
        conn.close()
        if watch_dir.exists():
            shutil.rmtree(watch_dir)


if __name__ == "__main__":
    sys.exit(main())
