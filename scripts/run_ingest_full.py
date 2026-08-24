"""Epica C.3 — Runner RIPARTIBILE per l'ingestion unificata del corpus.

Orchestra l'orchestratore GIA' ESISTENTE `src/ingestion/ingest_full.py`
(build_branches + ingest_document_full) su TUTTO il corpus, in modo ripartibile:
un run interrotto riprende da dove si era fermato saltando cio' che e' gia' fatto.

RIPARTIBILITA' PER-RAMO (non per-documento). La pipeline ha TRE rami indipendenti
con TRE fonti di verita' distinte:
    structured -> Postgres estrazione.righe_listino
    narrative  -> Qdrant aike_chunks_v2      (payload `doc`; nome NON hardcodato,
                                              viene da embedding.COLLECTION)
    visual     -> Qdrant aike_pages_visual   (payload `doc`)
La copertura si legge ramo per ramo: un documento e' pending se gli manca
ALMENO UNO dei rami richiesti (--branches), e di quel documento si esegue SOLO
il ramo mancante. Prima la copertura era l'UNIONE delle fonti (OR globale): un
documento con righe di listino ma ZERO chunk risultava "coperto" e il suo ramo
narrativo/visivo non entrava MAI in coda (misurati sul corpus reale: 28 doc
`version_status=current`, 21434 righe di listino, 0 chunk e 0 pagine visive).

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
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CENSUS_PATH = ROOT / "output" / "audit" / "b1_census_coverage.json"
DEFAULT_PROGRESS = "output/audit/c3_ingest_progress.jsonl"
BRANCHES = ("structured", "narrative", "visual")


def default_chunks_collection() -> str:
    """Collection narrativa reale: la costante usata dall'ingestion testuale."""
    from src.ingestion.embedding import COLLECTION
    return COLLECTION


def default_visual_collection() -> str:
    """Collection visiva reale: la costante usata dall'indice PixelRAG."""
    from src.retrieval.pixelrag.index import DEFAULT_VISUAL_COLLECTION
    return DEFAULT_VISUAL_COLLECTION


# ----------------------------------------------------------------------------
# Funzioni PURE (testabili a unita', nessun I/O)
# ----------------------------------------------------------------------------
def pending(candidates: list[str], covered: set[str]) -> list[str]:
    """Ritorna i candidates NON in covered, preservando l'ordine dato."""
    return [c for c in candidates if c not in covered]


def pending_branches(
    candidates: list[str],
    coverage: dict[str, set[str]],
    requested: set[str],
) -> dict[str, set[str]]:
    """{stem: rami RICHIESTI mancanti}, solo per gli stem cui ne manca almeno uno.

    `coverage` e' per-ramo (vedi `branch_coverage`): un ramo non presente nella
    mappa vale "niente coperto". Ordine dei candidates preservato (dict ordinato).
    Uno stem con TUTTI i rami richiesti gia' fatti NON compare: e' la
    ripartibilita', applicata al ramo e non piu' al documento intero.
    """
    out: dict[str, set[str]] = {}
    for c in candidates:
        missing = {b for b in requested if c not in coverage.get(b, set())}
        if missing:
            out[c] = missing
    return out


def resolve_branches(
    stem: str,
    branches_by_stem: dict[str, set[str]] | None,
    default_selected: set[str],
) -> set[str]:
    """Rami da eseguire per UNO stem: il piano per-documento se c'e', altrimenti
    la selezione globale (--branches)."""
    if branches_by_stem:
        sel = branches_by_stem.get(stem)
        if sel is not None:
            return set(sel)
    return set(default_selected)


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
    """Pagine di uno stem SECONDO IL CENSUS; 0 = "non censito", non "vuoto"."""
    return int(census.get(stem, 0))


def resolve_pages(
    stems: list[str],
    census: dict[str, int],
    page_counter: Callable[[str], int],
) -> dict[str, int]:
    """{stem: pagine} con il census come fonte primaria e il PDF REALE come
    fallback per gli stem non censiti.

    `b1_census_coverage.json` copre solo i documenti gia' indicizzati: per tutti
    gli altri `doc_pages` vale 0, e 0 veniva poi letto come "piccolo". E' la
    condizione di TUTTI e 28 i documenti mai entrati in coda, fra cui sei
    "mostri" (fino a 456 pagine): senza questo fallback `--exclude-giants` --
    che e' il DEFAULT -- non escludeva nulla di loro.

    `page_counter` NON viene chiamato per gli stem gia' censiti: il census resta
    la fonte primaria e non si riaprono PDF per nulla.
    """
    out: dict[str, int] = {}
    for stem in stems:
        n = doc_pages(census, stem)
        if n <= 0:
            n = int(page_counter(stem))
        out[stem] = n
    return out


def partition_giants(
    stems: list[str], pages: dict[str, int], threshold: int
) -> tuple[list[str], list[str]]:
    """Divide stems in (normali <= threshold, giganti > threshold).

    Soglia INCLUSIVA per i normali: pages == threshold -> normale.

    FAIL-CLOSED: uno stem di cui NON si conosce il numero di pagine (assente
    dalla mappa, 0 o negativo) finisce fra i GIGANTI, non fra i normali. Prima
    valeva il contrario, e siccome il census non copre i documenti non ancora
    indicizzati il default `--exclude-giants` non escludeva proprio quelli che
    doveva escludere: un run "prudente" avrebbe macinato un catalogo da 456
    pagine. Meglio saltare un documento di troppo che ingerirne uno enorme
    credendolo piccolo. Normalmente qui le pagine arrivano gia' risolte da
    `resolve_pages`, quindi questo ramo scatta solo su PDF illeggibili.
    """
    normali: list[str] = []
    giganti: list[str] = []
    for s in stems:
        n = int(pages.get(s, 0))
        if n <= 0 or n > threshold:
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


def pdf_page_count(path: str | None) -> int:
    """Pagine REALI di un PDF. 0 se il path manca o il file non e' leggibile.

    Lo 0 non e' un valore innocuo: `partition_giants` lo tratta come "pagine
    sconosciute" e manda lo stem fra i giganti (fail-closed)."""
    if not path:
        return 0
    try:
        import fitz

        doc = fitz.open(path)
        try:
            return int(doc.page_count)
        finally:
            doc.close()
    except Exception as exc:  # noqa: BLE001 -- un PDF rotto non ferma la conta
        print(f"WARN pagine reali {path}: {exc}", file=sys.stderr, flush=True)
        return 0


def _qdrant_docs(qc, collection: str) -> set[str]:
    """Stem distinti presenti in una collection Qdrant (payload key `doc`,
    i punti vecchi hanno doc_stem=None) via scroll paginato.

    Fail-soft: se la collection non esiste / Qdrant e' giu', WARN e insieme
    VUOTO -> quel ramo risulta da fare (mai "coperto per errore")."""
    docs: set[str] = set()
    try:
        offset = None
        while True:
            points, offset = qc.scroll(
                collection,
                limit=1000,
                offset=offset,
                with_payload=["doc"],
                with_vectors=False,
            )
            for p in points:
                doc = (p.payload or {}).get("doc")
                if doc:
                    docs.add(doc)
            if offset is None:
                break
    except Exception as exc:  # noqa: BLE001
        print(f"WARN copertura qdrant {collection}: {exc}", file=sys.stderr, flush=True)
    return docs


def branch_coverage(
    conn,
    qc,
    *,
    chunks_collection: str | None = None,
    visual_collection: str | None = None,
) -> dict[str, set[str]]:
    """Copertura PER-RAMO: {"structured"|"narrative"|"visual" -> set(stem)}.

    Tre fonti distinte, MAI unite fra loro: Postgres estrazione.righe_listino
    (DISTINCT doc_stem), Qdrant chunks, Qdrant pagine visive. Un OR fra queste
    dichiarerebbe "coperto" un documento con un solo ramo fatto."""
    structured: set[str] = set()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT doc_stem FROM estrazione.righe_listino")
            for (stem,) in cur.fetchall():
                if stem:
                    structured.add(stem)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN copertura postgres: {exc}", file=sys.stderr, flush=True)

    return {
        "structured": structured,
        "narrative": _qdrant_docs(qc, chunks_collection or default_chunks_collection()),
        "visual": _qdrant_docs(qc, visual_collection or default_visual_collection()),
    }


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
    branches_by_stem: dict[str, set[str]] | None = None,
) -> None:
    """Per ogni doc: build_branches + ingest_document_full; APPENDE una riga
    JSON a progress_path e stampa una riga tabella (flush). Un errore fatale
    su un doc NON ferma il batch.

    `branches_by_stem` e' il piano PER-DOCUMENTO (rami mancanti): ha la
    precedenza su `branches_selected`, che resta il default per gli stem non
    nel piano. I rami esclusi diventano no-op via `select_branches`."""
    import psycopg

    from src.backend.clients.postgres_client import conninfo
    from src.ingestion.ingest_full import build_branches, ingest_document_full

    prog = Path(progress_path)
    prog.parent.mkdir(parents=True, exist_ok=True)

    header = (f"{'doc_stem':45s} {'struct':>7s} {'narr':>6s} {'vis':>5s} "
              f"{'sec':>7s}  rami")
    print(header, flush=True)
    print("-" * len(header), flush=True)

    for stem in stems:
        pdf_path = paths.get(stem)
        t0 = time.perf_counter()
        if pdf_path is None:
            print(f"{stem:45s}  SKIP: nessun path nel corpus", flush=True)
            continue
        selected: set[str] | None = None
        if branches_selected is not None or branches_by_stem:
            selected = resolve_branches(
                stem, branches_by_stem, branches_selected or set(BRANCHES)
            )
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
                if selected is not None:
                    branches = select_branches(branches, selected)
                report = ingest_document_full(stem, **branches)
            secs = round(time.perf_counter() - t0, 2)
            row = {
                "doc_stem": report.doc_stem,
                "structured_rows": report.structured_rows,
                "narrative_chunks": report.narrative_chunks,
                "visual_pages": report.visual_pages,
                "scarti": report.scarti,
                "secondi": secs,
                "rami": sorted(selected) if selected is not None else sorted(BRANCHES),
            }
            with prog.open("a") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            flag = "  SCARTI:" + ",".join(report.scarti) if report.scarti else ""
            print(
                f"{stem:45s} {report.structured_rows:7d} "
                f"{report.narrative_chunks:6d} {report.visual_pages:5d} "
                f"{secs:7.2f}  {','.join(row['rami'])}{flag}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001 -- un doc rotto non ferma il batch
            secs = round(time.perf_counter() - t0, 2)
            row = {"doc_stem": stem, "error": f"{type(exc).__name__}: {exc}",
                   "secondi": secs,
                   "rami": sorted(selected) if selected is not None else sorted(BRANCHES)}
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
                    help="NON filtrare per copertura: processa TUTTI i candidates e su "
                         "ognuno esegue TUTTI i rami di --branches, anche quelli gia' fatti "
                         "(re-ingest volontario). Senza --force i rami gia' fatti sono saltati.")
    ap.add_argument("--branches", type=str, default="structured,narrative,visual",
                    help="Rami RICHIESTI (CSV: structured,narrative,visual). Un doc e' "
                         "pending se gliene manca almeno uno; di quel doc si esegue solo "
                         "il ramo mancante. Default: tutti e tre.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Stampa i pending che processerebbe ed ESCE senza scrivere nulla.")
    ap.add_argument("--progress", type=str, default=DEFAULT_PROGRESS,
                    help=f"File JSONL di progresso (default {DEFAULT_PROGRESS}).")
    ap.add_argument("--schema", type=str, default="estrazione")
    ap.add_argument("--chunks-collection", type=str, default=None)
    ap.add_argument("--visual-collection", type=str, default=None)
    args = ap.parse_args(argv)

    if args.only_giants and args.exclude_giants:
        print("ERRORE: --only-giants e --exclude-giants sono mutuamente esclusivi", file=sys.stderr)
        return 2

    candidates, corpus = _select_candidates(args)

    # rami richiesti (--branches): servono PRIMA della copertura, che e' per-ramo.
    branches_selected = {b.strip() for b in args.branches.split(",") if b.strip()}
    unknown = branches_selected - set(BRANCHES)
    if unknown:
        print(f"ERRORE: rami sconosciuti in --branches: {sorted(unknown)} "
              f"(validi: {sorted(BRANCHES)})", file=sys.stderr)
        return 2

    visual_collection = args.visual_collection or default_visual_collection()

    # copertura PER-RAMO (I/O, sola lettura)
    from src.backend.clients.postgres_client import conninfo
    from src.backend.clients.qdrant_client import get_qdrant_client
    import psycopg

    qc = get_qdrant_client()
    with psycopg.connect(conninfo()) as conn:
        coverage = branch_coverage(
            conn, qc,
            chunks_collection=args.chunks_collection,
            visual_collection=visual_collection,
        )

    # pending = manca almeno UNO dei rami richiesti; il valore e' cosa manca.
    missing = pending_branches(candidates, coverage, branches_selected)
    fully_covered = {s for s in candidates if s not in missing}
    todo = filter_by_coverage(candidates, fully_covered, force=args.force)
    # piano per-documento: senza --force solo i rami mancanti; con --force tutti
    # i rami richiesti (re-ingest volontario).
    plan = {
        s: (set(branches_selected) if args.force else missing[s]) for s in todo
    }

    # partizione giganti/normali: pagine dal census, fallback al PDF reale
    census = load_census()
    pages = resolve_pages(todo, census, lambda st: pdf_page_count(corpus.get(st)))
    non_censiti = [st for st in todo if doc_pages(census, st) <= 0]
    normali, giganti = partition_giants(todo, pages, args.giant_threshold)

    if args.only_giants:
        selected = giganti
    else:
        # default (nessun filtro) ed --exclude-giants coincidono: solo normali.
        selected = normali

    if args.limit and args.limit > 0:
        selected = selected[: args.limit]

    if args.dry_run:
        kind = "GIGANTI" if args.only_giants else "NORMALI"
        cov_line = " ".join(f"{b}={len(coverage[b])}" for b in BRANCHES)
        print(f"[dry-run] candidates={len(candidates)} coperti_per_ramo[{cov_line}] "
              f"completi={len(fully_covered)} force={args.force} todo={len(todo)} "
              f"normali={len(normali)} giganti={len(giganti)}")
        print(f"[dry-run] rami richiesti={sorted(branches_selected)}")
        if non_censiti:
            print(f"[dry-run] {len(non_censiti)} stem non nel census: pagine "
                  f"lette dal PDF reale (fallback)")
        print(f"[dry-run] processerebbe {len(selected)} documenti ({kind}):")
        for s in selected:
            rami = ",".join(sorted(plan.get(s, branches_selected)))
            print(f"  {s}  ({pages.get(s, 0)} pag)  rami_mancanti={rami}")
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
        visual_collection=visual_collection,
        progress_path=args.progress,
        branches_selected=branches_selected,
        branches_by_stem=plan,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
