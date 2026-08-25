"""Audit del ramo visivo del grafo (B1.d) — e la MATERIA PRIMA per tarare la soglia.

Cosa fa in piu' rispetto alla versione B1.d
-------------------------------------------
1. **Carica il `.env`** (`load_runtime_env`) prima di qualunque import che legga
   l'ambiente. Senza, lo script girava in una configurazione DIVERSA dalla
   produzione — `AIKE_VISUAL_RERANK` assente -> gate "auto" sulla RAM ->
   reranker visivo spento -> `visual_score_floor=None`, cioe' misurava l'unica
   configurazione **senza** il pavimento anti-confident-wrong, e non lo diceva.
   Stessa correzione gia' applicata a `golden_gate.py`/`smoke_e2e.py`.
2. Stampa e persiste la **configurazione effettiva** del run (`effective_config`
   sulle deps davvero costruite: osservata, non prevista).
3. Emette un file **una riga per query** (`--rows`, JSONL) con i punteggi delle
   prime K pagine PRIMA che la soglia decida, la posizione del bersaglio atteso
   e l'esito della soglia.

Perche' il punto 3
------------------
`VISUAL_SCORE_FLOOR = 0.20` e' stata scelta a intuito, perche' il numero su cui
decide non veniva registrato da nessuna parte: quando la soglia scarta,
`visual_pages` e' `[]` e per la metrica storica il caso e' un MISS
indistinguibile da "il motore non ha trovato la pagina". Sono due difetti
opposti. La riga per query mette accanto, per ogni domanda, il punteggio del
bersaglio GIUSTO e quello degli SBAGLIATI: e' da quella separazione che si
sceglie una soglia, non a intuito.

Lo script NON cambia ne' la soglia ne' `VISUAL_K`: costruisce il metro, non
muove l'ago.

Uso (carica i modelli: serve RAM libera)
----------------------------------------
    env -u PYTHONPATH PYTHONPATH=. .venv/bin/python scripts/audit_visual_graph_eval.py \
        --eval data/eval/eval_visual_v0.jsonl data/eval/eval_visual_clean.jsonl
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Il .env PRIMA di qualunque import che legga l'ambiente (settings pydantic, gate
# hardware del reranker). E' il difetto noto di questo script: senza, girava col
# reranker spento e quindi SENZA il pavimento che stiamo misurando.
from src.common.run_config import load_runtime_env  # noqa: E402

load_runtime_env()

from src.common.run_config import effective_config, format_config_banner  # noqa: E402
from src.graph import visual_trace  # noqa: E402

DEFAULT_EVAL = [ROOT / "data" / "eval" / "eval_visual_v0.jsonl"]
DEFAULT_OUT = ROOT / "output" / "audit" / "b1d_visual_graph_eval.json"
DEFAULT_ROWS = ROOT / "output" / "audit" / "visual_score_rows.jsonl"

# Esito registrato quando il trace non ha prodotto nulla per quella query: NON e'
# un rifiuto, e non va confuso con uno. Succede se la registrazione e' spenta o
# se la query non e' finita sul ramo visivo (router).
OUTCOME_UNRECORDED = "non_registrato"


def calc_metrics(ranks: list[int | None], ks: tuple[int, ...] = (1, 3, 5)) -> dict[str, float]:
    n = len(ranks)
    if not n:
        return {}
    m = {f"recall@{k}": sum(1 for r in ranks if r and r <= k) / n for k in ks}
    m["mrr"] = sum(1/r for r in ranks if r) / n
    return m


def get_rank(candidates: list[tuple[str, int]], expected) -> int | None:
    for i, c in enumerate(candidates, 1):
        if c in expected:
            return i
    return None


# --------------------------------------------------------------- caricamento

def load_pairs(paths) -> list[tuple[str, dict]]:
    """(nome del truth set, item). Il nome resta nella riga: senza, un run su
    piu' truth set non e' piu' separabile per set."""
    out = []
    for p in paths:
        p = Path(p)
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append((p.stem, json.loads(line)))
    return out


def indexed_pages(client, collection) -> set[tuple[str, int]]:
    """Tutte le `(doc, page)` presenti nell'indice visivo, paginando lo scroll.

    Paginato apposta: con `limit=10000` secco un indice piu' grande verrebbe
    troncato in silenzio e i bersagli oltre il taglio sembrerebbero mancanti —
    un difetto del metro travestito da difetto del motore.
    """
    pagine: set[tuple[str, int]] = set()
    offset = None
    while True:
        punti, offset = client.scroll(collection, limit=5000, offset=offset,
                                      with_payload=True, with_vectors=False)
        if not punti:
            break
        for pt in punti:
            pl = pt.payload or {}
            doc, page = pl.get("doc"), pl.get("page")
            if doc is not None and page is not None:
                pagine.add((doc, int(page)))
        if offset is None:
            break
    return pagine


# ------------------------------------------------------------------- la riga

def _pages_payload(rec) -> list[dict]:
    return [{"doc": d, "page": p, "score": s} for d, p, s in rec.pages]


def build_row(source: str, pair: dict, *, role, state: dict, rec, expected,
              latency: float) -> dict:
    """Una riga = tutto quel che serve per tarare la soglia su QUESTA query.

    Due letture affiancate, ed e' il punto dell'intero esercizio:
      - `rank`/`shown`  = cosa ha visto l'utente (metrica storica, POST soglia);
      - `*_pre_floor`   = cosa aveva trovato il motore PRIMA della soglia.
    Quando le due divergono, la soglia sta buttando via una risposta giusta.
    """
    shown = [(p.doc, p.page) for p in state.get("visual_pages") or []]
    rank = get_rank(shown, expected)
    pre = [(d, p) for d, p, _ in rec.pages] if rec is not None else []
    rank_pre = get_rank(pre, expected)
    return {
        "qa_id": pair.get("qa_id"),
        "source": source,
        "query": pair.get("query"),
        "lang": pair.get("lang"),
        "role": role,
        "status": "EVALUATED",
        "expected": sorted([list(e) for e in expected]),
        # --- prima della soglia (dal trace: e' l'informazione che si perdeva)
        "topk_pre_floor": _pages_payload(rec) if rec is not None else [],
        "top_score_pre_floor": rec.top_score if rec is not None else None,
        "target_rank_pre_floor": rank_pre,
        "target_in_topk_pre_floor": rank_pre is not None,
        "target_score": (rec.pages[rank_pre - 1][2]
                         if rec is not None and rank_pre is not None else None),
        "score_floor": rec.score_floor if rec is not None else None,
        "floor_gate_armed": rec.floor_gate_armed if rec is not None else None,
        "reranker_active": rec.reranker_active if rec is not None else None,
        "floor_outcome": rec.outcome if rec is not None else OUTCOME_UNRECORDED,
        # --- dopo la soglia (e dopo il giudice): cio' che l'utente vede
        "shown": [list(s) for s in shown],
        "rank": rank,
        "hit_at": {str(k): bool(rank and rank <= k) for k in (1, 3, 5)},
        "level": state.get("level"),
        "latency_s": round(latency, 3),
    }


def evaluate(pairs, *, indexed, doc_roles, answer_fn, deps_fn, trace_fn,
             log=print) -> list[dict]:
    """Esegue il truth set e produce le righe. I/O tutto iniettato -> testabile
    senza modelli."""
    rows = []
    for source, pair in pairs:
        qa_id = pair.get("qa_id")
        expected = {(c["doc"], p) for c in pair["expected"] for p in c["pages"]}

        missing = sorted([d, p] for d, p in expected if (d, p) not in indexed)
        if missing:
            # Bersaglio assente dall'indice: recall 0 che sembra un difetto del
            # motore ed e' un difetto del METRO. Gia' successo in questo progetto.
            log(f"[!] {qa_id} NON_VALUTABILE: pagine mancanti {missing}")
            rows.append({"qa_id": qa_id, "source": source,
                         "query": pair.get("query"), "lang": pair.get("lang"),
                         "status": "NON_VALUTABILE", "missing_pages": missing})
            continue

        expected_doc = pair["expected"][0]["doc"]
        role = doc_roles.get(expected_doc)
        if not role:
            log(f"[!] {qa_id} NON_VALUTABILE: ruolo non trovato per doc '{expected_doc}'")
            rows.append({"qa_id": qa_id, "source": source,
                         "query": pair.get("query"), "lang": pair.get("lang"),
                         "status": "NON_VALUTABILE",
                         "reason": f"Role missing for doc {expected_doc}"})
            continue

        log(f"[{qa_id}] {source} | '{pair['query']}' (role: {role})")
        trace_fn()          # scarta i residui: il trace di una query non deve
                            # sporcare la successiva
        deps = deps_fn(role)
        t0 = time.perf_counter()
        state = answer_fn(pair["query"], role, deps=deps)
        latency = time.perf_counter() - t0

        recs = trace_fn()
        rec = recs[-1] if recs else None   # l'ultima e' quella di questa query
        rows.append(build_row(source, pair, role=role, state=state, rec=rec,
                              expected=expected, latency=latency))
    return rows


def write_rows(rows, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# ------------------------------------------------------------------ riepilogo

def summarize(rows, log=print) -> dict:
    valutate = [r for r in rows if r.get("status") == "EVALUATED"]
    ranks = [r["rank"] for r in valutate]
    m_all = calc_metrics(ranks)
    latencies = sorted(r["latency_s"] for r in valutate)
    p50 = latencies[len(latencies)//2] if latencies else 0

    log("\n--- RISULTATI RAMO VISUAL DEL GRAFO ---")
    for k, v in m_all.items():
        log(f"  {k}: {v:.3f}")
    log(f"  latency_p50: {p50:.3f}s")

    log("\n--- DETTAGLIO PER LINGUA ---")
    for lang in sorted({r["lang"] for r in valutate if r.get("lang")}):
        l_ranks = [r["rank"] for r in valutate if r.get("lang") == lang]
        lm = calc_metrics(l_ranks)
        log(f"[{lang}] (n={len(l_ranks)}) R@5={lm.get('recall@5', 0):.3f} "
            f"MRR={lm.get('mrr', 0):.3f}")

    # Il conto che motiva tutto l'esercizio: quante risposte GIUSTE la soglia
    # sta buttando via, e quante ne salva. Nessuna soglia viene toccata qui.
    scartate = [r for r in valutate if r["floor_outcome"] == visual_trace.OUTCOME_REJECTED]
    scartate_giuste = [r for r in scartate if r["target_in_topk_pre_floor"]]
    non_registrate = [r for r in valutate if r["floor_outcome"] == OUTCOME_UNRECORDED]
    log("\n--- SOGLIA (osservata, NON modificata) ---")
    log(f"  query scartate dalla soglia : {len(scartate)}/{len(valutate)}")
    log(f"    di cui col bersaglio fra le pagine pre-soglia: {len(scartate_giuste)}")
    if non_registrate:
        log(f"  [!] {len(non_registrate)} query senza trace "
            f"(registrazione spenta o query non instradata al visivo)")
    return {"metrics_overall": m_all, "latency_p50": p50,
            "floor_rejected": len(scartate),
            "floor_rejected_with_target": len(scartate_giuste),
            "unrecorded": len(non_registrate),
            "evaluated": len(valutate), "total": len(rows)}


# ---------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval", nargs="+", default=[str(p) for p in DEFAULT_EVAL],
                    help="uno o piu' truth set visivi (JSONL)")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="riepilogo JSON")
    ap.add_argument("--rows", default=str(DEFAULT_ROWS),
                    help="una riga JSON per query (la materia prima della curva)")
    args = ap.parse_args(argv)

    from src.backend.clients.postgres_client import get_pg_conn
    from src.backend.clients.qdrant_client import get_qdrant_client
    from src.graph.pipeline import answer
    from src.retrieval.pixelrag.index import DEFAULT_VISUAL_COLLECTION
    from src.serving.session_deps import get_deps

    # La registrazione degli score e' SPENTA di default in produzione: qui la
    # accendiamo apposta, perche' questo script E' lo strumento di misura.
    os.environ[visual_trace.TRACE_ENV] = "1"
    visual_trace.reset()

    pairs = load_pairs(args.eval)

    client = get_qdrant_client()
    indexed = indexed_pages(client, DEFAULT_VISUAL_COLLECTION)
    print(f"[eval-vis] query={len(pairs)} pagine indicizzate={len(indexed)}")

    doc_roles = {}
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT d.slug, r.role FROM public.documents d "
                        "JOIN public.document_roles r ON d.id = r.document_id")
            for row in cur.fetchall():
                doc_roles[row[0]] = row[1]

    deps_cache: dict[str, object] = {}

    def _deps(role):
        if role not in deps_cache:
            deps_cache[role] = get_deps(role)
        return deps_cache[role]

    # La configurazione va fotografata sulle deps DAVVERO costruite: l'env puo'
    # dire reranker ON e l'init essere fallito, e allora il pavimento reale e'
    # None. Un audit deve dire la verita', non l'intenzione.
    primo_ruolo = next(iter(doc_roles.values()), "tecnico")
    cfg = effective_config(_deps(primo_ruolo))
    print(format_config_banner(cfg))

    rows = evaluate([(s, p) for s, p in pairs], indexed=indexed,
                    doc_roles=doc_roles, answer_fn=answer, deps_fn=_deps,
                    trace_fn=visual_trace.take)
    metrics = summarize(rows)

    write_rows(rows, args.rows)
    out_file = Path(args.out)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8") as f:
        json.dump({"config": cfg, "eval_files": args.eval, **metrics,
                   "rows_file": str(args.rows), "results": rows}, f, indent=2,
                  ensure_ascii=False)
    print(f"\n[eval-vis] riepilogo -> {out_file}")
    print(f"[eval-vis] una riga per query -> {args.rows}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
