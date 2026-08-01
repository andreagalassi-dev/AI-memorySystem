"""Epica C.3 — Runner RIPARTIBILE per l'ingestion unificata del corpus.

Orchestra l'orchestratore GIA' ESISTENTE `src/ingestion/ingest_full.py`
(build_branches + ingest_document_full) su TUTTO il corpus, in modo ripartibile:
i documenti gia' coperti (Postgres estrazione.righe_listino OR Qdrant aike_chunks)
vengono saltati, cosi' un run interrotto puo' riprendere da dove si era fermato.

Sicurezza: `--dry-run` NON scrive nulla (elenca solo i pending). I run veri
li lancia il reviewer con snapshot.

SCELTA di DEFAULT (nessun filtro): processa i pending NON giganti. I documenti
"giganti" (pagine > --giant-threshold, default 100) sono ESCLUSI di default perche'
costosi/rischiosi; per processarli serve `--only-giants`. `--exclude-giants` e'
percio' il comportamento di default reso esplicito.

Uso:
    env -u PYTHONPATH OLLAMA_HOST=http://localhost:11435 PYTHONPATH=. \\
        .venv/bin/python scripts/run_ingest_full.py --dry-run
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CENSUS_PATH = ROOT / "output" / "audit" / "b1_census_coverage.json"
DEFAULT_PROGRESS = "output/audit/c3_ingest_progress.jsonl"
CHUNKS_COLLECTION = "aike_chunks"


# ----------------------------------------------------------------------------
# Funzioni PURE (testabili a unita', nessun I/O)
# ----------------------------------------------------------------------------
def pending(candidates: list[str], covered: set[str]) -> list[str]:
    """Ritorna i candidates NON in covered, preservando l'ordine dato."""
    return [c for c in candidates if c not in covered]


def filter_by_coverage(
    candidates: list[str], covered: set[str], *, force: bool
) -> list[str]:
    """Applica (o no) il filtro di copertura.

    force=False -> comportamento storico: scarta i coperti (pending()).
    force=True  -> ritorna i candidates COSI' COME SONO (uno stem gia'
    coperto passa comunque: serve per completare rami mancanti di un doc
    gia' parzialmente ingerito)."""
    if force:
        return list(candidates)
    return pending(candidates, covered)


def select_branches(branches: dict, selected: set[str]) -> dict:
    """Ritorna una copia di `branches` in cui i rami NON in `selected`
    sono sostituiti da un no-op `lambda: 0`.

    I rami esclusi non vengono quindi mai eseguiti: es. `selected =
    {narrative, visual}` rende lo strutturato un no-op (non chiama
    run_document_massive, ~3h risparmiate). Le chiavi sono preservate."""
    return {
        name: (fn if name in selected else (lambda: 0))
        for name, fn in branches.items()
    }


def doc_pages(census: dict[str, int], stem: str) -> int:
    """Numero pagine di uno stem dal census; 0 se non censito."""
    return int(census.get(stem, 0))


def partition_giants(
    stems: list[str], pages: dict[str, int], threshold: int
) -> tuple[list[str], list[str]]:
    """Divide stems in (normali <= threshold, giganti > threshold).

    Soglia INCLUSIVA per i normali: pages == threshold -> normale.
    Uno stem senza census (doc_pages 0) e' normale.
    """
    normali: list[str] = []
    giganti: list[str] = []
    for s in stems:
        if doc_pages(pages, s) > threshold:
            giganti.append(s)
        else:
            normali.append(s)
    return normali, giganti


# ----------------------------------------------------------------------------
# I/O (non testato a unita': DB / filesystem)
# ----------------------------------------------------------------------------
def resolve_corpus() -> dict[str, str]:
    """Mappa stem -> path via glob('corpus/**/*.pdf', recursive=True)."""
    out: dict[str, str] = {}
    for p in sorted(glob.glob(str(ROOT / "corpus" / "**" / "*.pdf"), recursive=True)):
        stem = Path(p).stem
        # primo path vince (ordine stabile via sorted); non sovrascrive.
        out.setdefault(stem, p)
    return out


def load_census() -> dict[str, int]:
    """Legge output/audit/b1_census_coverage.json -> {stem: pages}."""
    if not CENSUS_PATH.exists():
        return {}
    data = json.loads(CENSUS_PATH.read_text())
    return {row["stem"]: int(row["pages"]) for row in data}


def covered_stems(conn, qc) -> set[str]:
    """Unione dei doc gia' coperti: Postgres estrazione.righe_listino
    (DISTINCT doc_stem) E Qdrant aike_chunks (payload key `doc`, i chunk
    vecchi hanno doc_stem=None) via scroll."""
    covered: set[str] = set()

    # Postgres: strutturato
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT doc_stem FROM estrazione.righe_listino")
            for (stem,) in cur.fetchall():
                if stem:
                    covered.add(stem)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN covered_stems postgres: {exc}", file=sys.stderr, flush=True)

    # Qdrant: narrativo (payload `doc`)
    try:
        offset = None
        while True:
            points, offset = qc.scroll(
                CHUNKS_COLLECTION,
                limit=1000,
                offset=offset,
                with_payload=["doc"],
                with_vectors=False,
            )
            for p in points:
                doc = (p.payload or {}).get("doc")
                if doc:
                    covered.add(doc)
            if offset is None:
                break
    except Exception as exc:  # noqa: BLE001
        print(f"WARN covered_stems qdrant: {exc}", file=sys.stderr, flush=True)

    return covered


# ----------------------------------------------------------------------------
# Glue sull'orchestratore GIA' testato (non testato a unita')
# ----------------------------------------------------------------------------
def load_deps():
    """Carica UNA volta embedder + multiplexer + extractor + qdrant client.

    Wiring IDENTICO a tests/test_ingest_full_wiring.py."""
    from src.backend.clients.qdrant_client import get_qdrant_client
    from src.ingestion.extraction.extractor import FakeExtractor
    from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder
    from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer

    qc = get_qdrant_client()
    mux = ModelMultiplexer(FakeModelRuntime(), max_resident=1, ctx_cap=16000)
    extractor = FakeExtractor({})
    embedder = Qwen3VLEmbedder()
    return {
        "qc": qc,
        "mux": mux,
        "extractor": extractor,
        "embedder": embedder,
        "model_name": "glm-ocr",
    }


def run_batch(
    stems: list[str],
    paths: dict[str, str],
    deps: dict,
    *,
    schema: str,
    chunks_collection: str | None,
    visual_collection: str,
    progress_path: str,
    role: str = "tecnico",
    branches_selected: set[str] | None = None,
) -> None:
    """Per ogni doc: build_branches + ingest_document_full; APPENDE una riga
    JSON a progress_path e stampa una riga tabella (flush). Un errore fatale
    su un doc NON ferma il batch."""
    import psycopg

    from src.backend.clients.postgres_client import conninfo
    from src.ingestion.ingest_full import build_branches, ingest_document_full

    prog = Path(progress_path)
    prog.parent.mkdir(parents=True, exist_ok=True)

    header = f"{'doc_stem':45s} {'struct':>7s} {'narr':>6s} {'vis':>5s} {'sec':>7s}"
    print(header, flush=True)
    print("-" * len(header), flush=True)

    for stem in stems:
        pdf_path = paths.get(stem)
        t0 = time.perf_counter()
        if pdf_path is None:
            print(f"{stem:45s}  SKIP: nessun path nel corpus", flush=True)
            continue
        try:
            with psycopg.connect(conninfo()) as conn:
                branches = build_branches(
                    pdf_path,
                    stem,
                    role,
                    conn=conn,
                    multiplexer=deps["mux"],
                    model_name=deps["model_name"],
                    extractor=deps["extractor"],
                    embedder=deps["embedder"],
                    qdrant_client=deps["qc"],
                    schema=schema,
                    chunks_collection=chunks_collection,
                    visual_collection=visual_collection,
                )
                if branches_selected is not None:
                    branches = select_branches(branches, branches_selected)
                report = ingest_document_full(stem, **branches)
            secs = round(time.perf_counter() - t0, 2)
            row = {
                "doc_stem": report.doc_stem,
                "structured_rows": report.structured_rows,
                "narrative_chunks": report.narrative_chunks,
                "visual_pages": report.visual_pages,
                "scarti": report.scarti,
                "secondi": secs,
            }
            with prog.open("a") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            flag = "  SCARTI:" + ",".join(report.scarti) if report.scarti else ""
            print(
                f"{stem:45s} {report.structured_rows:7d} "
                f"{report.narrative_chunks:6d} {report.visual_pages:5d} "
                f"{secs:7.2f}{flag}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 -- un doc rotto non ferma il batch
            secs = round(time.perf_counter() - t0, 2)
            row = {"doc_stem": stem, "error": f"{type(exc).__name__}: {exc}", "secondi": secs}
            with prog.open("a") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            print(f"{stem:45s}  ERRORE: {type(exc).__name__}: {exc}", flush=True)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _select_candidates(args) -> tuple[list[str], dict[str, str]]:
    corpus = resolve_corpus()
    if args.stems:
        wanted = [s.strip() for s in args.stems.split(",") if s.strip()]
        missing = [s for s in wanted if s not in corpus]
        if missing:
            print(f"WARN stems non nel corpus: {missing}", file=sys.stderr, flush=True)
        return [s for s in wanted if s in corpus], corpus
    return sorted(corpus.keys()), corpus


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Runner ripartibile ingestion unificata (Epica C.3).")
    ap.add_argument("--exclude-giants", action="store_true",
                    help="Processa solo i documenti normali (default gia' esclude i giganti).")
    ap.add_argument("--only-giants", action="store_true",
                    help="Processa solo i documenti giganti (> --giant-threshold pagine).")
    ap.add_argument("--giant-threshold", type=int, default=100,
                    help="Soglia pagine oltre cui un doc e' 'gigante' (default 100).")
    ap.add_argument("--limit", type=int, default=0, help="Processa al massimo N documenti (0 = tutti).")
    ap.add_argument("--stems", type=str, default="",
                    help="Sottoinsieme esplicito, stem separati da virgola.")
    ap.add_argument("--force", action="store_true",
                    help="NON filtrare i candidates per copertura: processa anche i doc "
                         "gia' coperti (per completare rami mancanti).")
    ap.add_argument("--branches", type=str, default="structured,narrative,visual",
                    help="Rami da eseguire (CSV: structured,narrative,visual). I rami "
                         "esclusi diventano no-op. Default: tutti e tre.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Stampa i pending che processerebbe ed ESCE senza scrivere nulla.")
    ap.add_argument("--progress", type=str, default=DEFAULT_PROGRESS,
                    help=f"File JSONL di progresso (default {DEFAULT_PROGRESS}).")
    ap.add_argument("--schema", type=str, default="estrazione")
    ap.add_argument("--chunks-collection", type=str, default=None)
    ap.add_argument("--visual-collection", type=str, default="aike_pages_visual")
    args = ap.parse_args(argv)

    if args.only_giants and args.exclude_giants:
        print("ERRORE: --only-giants e --exclude-giants sono mutuamente esclusivi", file=sys.stderr)
        return 2

    candidates, corpus = _select_candidates(args)

    # copertura (I/O)
    from src.backend.clients.postgres_client import conninfo
    from src.backend.clients.qdrant_client import get_qdrant_client
    import psycopg

    qc = get_qdrant_client()
    with psycopg.connect(conninfo()) as conn:
        covered = covered_stems(conn, qc)

    todo = filter_by_coverage(candidates, covered, force=args.force)

    # rami selezionati (--branches)
    valid_branches = {"structured", "narrative", "visual"}
    branches_selected = {b.strip() for b in args.branches.split(",") if b.strip()}
    unknown = branches_selected - valid_branches
    if unknown:
        print(f"ERRORE: rami sconosciuti in --branches: {sorted(unknown)} "
              f"(validi: {sorted(valid_branches)})", file=sys.stderr)
        return 2

    # partizione giganti/normali
    census = load_census()
    normali, giganti = partition_giants(todo, census, args.giant_threshold)

    if args.only_giants:
        selected = giganti
    else:
        # default (nessun filtro) ed --exclude-giants coincidono: solo normali.
        selected = normali

    if args.limit and args.limit > 0:
        selected = selected[: args.limit]

    if args.dry_run:
        kind = "GIGANTI" if args.only_giants else "NORMALI"
        print(f"[dry-run] candidates={len(candidates)} coperti={len(covered)} "
              f"force={args.force} todo={len(todo)} normali={len(normali)} giganti={len(giganti)}")
        print(f"[dry-run] rami={sorted(branches_selected)}")
        print(f"[dry-run] processerebbe {len(selected)} documenti ({kind}):")
        for s in selected:
            print(f"  {s}  ({doc_pages(census, s)} pag)")
        print("[dry-run] NESSUNA scrittura eseguita.")
        return 0

    if not selected:
        print("Niente da fare: 0 pending selezionati.")
        return 0

    deps = load_deps()
    run_batch(
        selected,
        corpus,
        deps,
        schema=args.schema,
        chunks_collection=args.chunks_collection,
        visual_collection=args.visual_collection,
        progress_path=args.progress,
        branches_selected=branches_selected,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
