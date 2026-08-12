"""Metro NARRATIVO e2e: R@1/R@3/R@10 + tasso di risposta corretta, PER LINGUA.

Set: `data/eval/eval_narrative_v1.jsonl` (costruito da
`scripts/build_narrative_eval.py`, provenienza dichiarata item per item).

Due modalita', stesso set e stesse metriche di retrieval:

  --mode e2e        (default) passa da `answer(query, role)`: e' la produzione,
                    LLM incluso. Misura ANCHE il tasso di risposta corretta
                    con citazione. ~45 s/item.
  --mode retrieval  salta la sintesi e riproduce fedelmente i due nodi che
                    determinano i candidati (`expansion_node` + `retrieval_node`
                    di src/graph/pipeline.py): stessa `deps.expand`, stessa
                    `deps.retrieve`, stessa condizione `route == "natural"`.
                    ~1 s/item: e' la modalita' per gli A/B sul recall.
  --mode candidates guarda PRIMA del cross-encoder, sulla lista di candidati
                    RRF (`search(..., limit=RERANK_TOPK)`), e fa l'A/B in un
                    colpo solo: stessa query con e senza i termini numerici.
                    Serve a separare due domande che il modo 'retrieval'
                    confonde: l'espansione ha portato il documento tra i
                    candidati? e il reranker ce l'ha tenuto? Senza questa
                    distinzione un fix che funziona e un fix che non funziona
                    producono lo stesso numero.

Metriche
--------
retrieval  rank = prima posizione in `state["evidence"]` con (doc,pagina) tra i
           target dell'item. R@k = quota di item con rank <= k.
e2e        cited  = almeno una citazione su un (doc,pagina) target;
           ok     = cited E livello non di rifiuto (ALTA/MEDIA);
           wrong  = livello ALTA/MEDIA con citazioni MA nessuna sul target
                    (il caso che il progetto vieta: sbagliato in silenzio).

Le metriche sono separate per lingua (regola di progetto) e riportate anche sul
sottoinsieme `focused` (<= 3 pagine target): un item con 30 pagine accettabili
e' piu' facile da centrare, e mescolarlo con gli altri gonfia il numero.

RUMORE DI FONDO — misurato, da conoscere prima di leggere un delta
------------------------------------------------------------------
  retrieval  STABILE. Due run consecutivi a codice invariato danno 56/56 item
             con stesso rank e stesso top-3. Un delta qui e' segnale.
  candidati  INSTABILE nell'ORDINE. La fusione RRF spareggia i pari-merito in
             modo non deterministico (verificato: 5 run della stessa query
             danno permutazioni diverse delle prime posizioni). Le colonne
             C@1/C@3 oscillano da sole: leggere solo le righe per-item degli
             item toccati, non i totali per lingua.
  e2e        INSTABILE di almeno 1 item. Con evidenza IDENTICA l'LLM puo'
             cambiare la citazione: NARR-W003 e' risultato cited=True in 4
             osservazioni su 5 a parita' di tutto. Un delta e2e di +/-1 item
             (~0.018) NON e' attribuibile a una modifica. Il golden gate
             gestisce la stessa cosa girando le narrative 3 volte.

La configurazione effettiva del run viene STAMPATA e persistita nel JSON
(`src/common/run_config.py`): un numero senza la sua configurazione e' un
aneddoto, non una misura.

Uso:
  .venv/bin/python scripts/eval_narrative.py --mode retrieval --tag before
  .venv/bin/python scripts/eval_narrative.py --mode e2e --tag before
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.run_config import load_runtime_env  # noqa: E402

load_runtime_env()

from src.common.run_config import effective_config, format_config_banner  # noqa: E402

SET = ROOT / "data" / "eval" / "eval_narrative_v1.jsonl"
OUT_DIR = ROOT / "output" / "eval"
KS = (1, 3, 10)
REFUSAL_LEVELS = {"BASSA", "REFUSAL", "RIFIUTO", ""}


def _targets(item: dict) -> set[tuple[str, int]]:
    return {(t["doc"], int(t["page"])) for t in item["targets"]}


def _rank(evidence, targets: set[tuple[str, int]]) -> int | None:
    for i, e in enumerate(evidence, start=1):
        try:
            key = (e.doc, int(e.page))
        except (TypeError, ValueError):
            continue
        if key in targets:
            return i
    return None


def _citation_hit(citations, targets: set[tuple[str, int]]) -> bool:
    for c in citations:
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            doc, page = c[0], c[1]
        elif isinstance(c, dict):
            doc, page = c.get("doc"), c.get("page")
        else:
            doc, page = getattr(c, "doc", None), getattr(c, "page", None)
        try:
            if (doc, int(page)) in targets:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _run_retrieval_only(deps, query: str) -> dict:
    """Riproduce expansion_node + retrieval_node (pipeline.py) senza LLM."""
    from src.retrieval.router import classify_query

    route = classify_query(query).query_type
    terms: list[str] = []
    if deps.expand is not None and route == "natural":
        try:
            terms = deps.expand(query)
        except Exception:
            terms = []
    ev, _children = (deps.retrieve(query, terms) if terms
                     else deps.retrieve(query))
    return {"route": route, "evidence": ev, "expansion_terms": terms}


def _run_candidates(query: str) -> dict:
    """A/B sui CANDIDATI (pre cross-encoder): con e senza i termini numerici.

    Le due varianti girano nello stesso processo sulla stessa query: la sola
    differenza e' la presenza dei termini emessi da `expand_number_words`.
    """
    from src.backend.clients.qdrant_client import get_qdrant_client
    from src.retrieval.expansion import expand_number_words, expand_query
    from src.retrieval.rerank import RERANK_TOPK
    from src.retrieval.router import classify_query
    from src.retrieval.search import search

    qc = get_qdrant_client()
    route = classify_query(query).query_type
    if route != "natural":
        terms_all: list[str] = []
    else:
        terms_all = expand_query(query)
    numeric = set(expand_number_words(query))
    terms_base = [t for t in terms_all if t not in numeric]

    def cands(terms):
        hits = search(qc, query, "routed", limit=RERANK_TOPK,
                      extra_terms=terms or None)
        return [(h.payload.get("doc"), h.payload.get("page")) for h in hits]

    return {"route": route, "terms_base": terms_base,
            "terms_all": terms_all,
            "numeric": sorted(numeric),
            "base": cands(terms_base), "expanded": cands(terms_all)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("e2e", "retrieval", "candidates"),
                    default="e2e")
    ap.add_argument("--tag", default=None, help="etichetta del run (nome file)")
    ap.add_argument("--set", dest="set_path", default=str(SET))
    ap.add_argument("--ids", default=None, help="lista item_id separati da virgola")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()

    items = [json.loads(line) for line in Path(args.set_path).open(encoding="utf-8")]
    if args.ids:
        want = {s.strip() for s in args.ids.split(",")}
        items = [i for i in items if i["item_id"] in want]
    if args.limit:
        items = items[: args.limit]

    from src.serving.session_deps import get_deps
    from src.graph.pipeline import answer

    deps_cache: dict = {}
    cfg_printed = False
    rows: list[dict] = []
    t_start = time.time()

    for it in items:
        role = it["role"]
        if role not in deps_cache:
            deps_cache[role] = get_deps(role)
        deps = deps_cache[role]
        if not cfg_printed:
            cfg = effective_config(deps)
            print(format_config_banner(cfg))
            print(f"  modalita'            : {args.mode}")
            print(f"  set                  : {args.set_path}  ({len(items)} item)")
            print("=" * 72, flush=True)
            cfg_printed = True

        tg = _targets(it)
        t0 = time.time()
        if args.mode == "candidates":
            st = _run_candidates(it["query"])

            def pos(lst):
                for i, (d, p) in enumerate(lst, start=1):
                    try:
                        if (d, int(p)) in tg:
                            return i
                    except (TypeError, ValueError):
                        continue
                return None

            rb, ra = pos(st["base"]), pos(st["expanded"])
            rows.append({
                "item_id": it["item_id"], "lang": it["lang"],
                "focused": it["focused"], "n_target_pages": it["n_target_pages"],
                "query": it["query"], "route": st["route"],
                "rank": rb, "rank_expanded": ra,
                "numeric_terms": st["numeric"],
                "latency_s": round(time.time() - t0, 2),
            })
            flag = ""
            if rb != ra:
                flag = "  <<< " + ("MIGLIORA" if (ra and (not rb or ra < rb))
                                   else "PEGGIORA")
            print(f"[{it['item_id']:<12}] {it['lang']} candidati "
                  f"base={rb} espansi={ra}{flag}  {st['numeric']}", flush=True)
            continue
        if args.mode == "e2e":
            st = answer(it["query"], role, deps=deps)
            evidence = st.get("evidence") or []
            citations = st.get("citations") or []
            level = (st.get("final_level") or "").upper()
            route = st.get("route")
            terms = st.get("expansion_terms") or []
        else:
            st = _run_retrieval_only(deps, it["query"])
            evidence = st["evidence"]
            citations, level = [], None
            route = st["route"]
            terms = st["expansion_terms"]
        dt = time.time() - t0

        rank = _rank(evidence, tg)
        cited = _citation_hit(citations, tg) if args.mode == "e2e" else None
        row = {
            "item_id": it["item_id"], "lang": it["lang"],
            "focused": it["focused"], "n_target_pages": it["n_target_pages"],
            "query": it["query"], "targets": it["targets"],
            "route": route, "rank": rank, "level": level, "cited": cited,
            "expansion_terms": terms,
            "n_evidence": len(evidence),
            "top3": [[e.doc, e.page, e.code] for e in evidence[:3]],
            "latency_s": round(dt, 2),
        }
        if args.mode == "e2e":
            row["ok"] = bool(cited) and level not in REFUSAL_LEVELS
            row["wrong_confident"] = (level not in REFUSAL_LEVELS
                                      and bool(citations) and not cited)
        rows.append(row)
        print(f"[{it['item_id']:<12}] {it['lang']} rank={rank} "
              f"level={level} cited={cited} {dt:5.1f}s  {it['query'][:60]!r}",
              flush=True)

    elapsed = time.time() - t_start
    report = _format(rows, args.mode)
    print()
    print(report)
    print(f"\ntempo totale: {elapsed/60:.1f} min")

    tag = args.tag or datetime.now().strftime("%Y%m%d_%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"narrative_{args.mode}_{tag}.json"
    out.write_text(json.dumps(
        {"config": effective_config(), "mode": args.mode,
         "set": args.set_path, "generated_at": datetime.now().isoformat(timespec="seconds"),
         "elapsed_s": round(elapsed, 1), "rows": rows},
        indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"dettaglio -> {out}")
    return 0


def _agg(rows: list[dict], mode: str) -> dict:
    n = len(rows)
    if not n:
        return {}
    out = {"n": n}
    for k in KS:
        out[f"R@{k}"] = round(sum(1 for r in rows if r["rank"] and r["rank"] <= k) / n, 3)
    out["MRR"] = round(sum(1 / r["rank"] for r in rows if r["rank"]) / n, 3)
    if mode == "e2e":
        out["e2e_ok"] = round(sum(1 for r in rows if r.get("ok")) / n, 3)
        out["wrong"] = sum(1 for r in rows if r.get("wrong_confident"))
    return out


def _table(title: str, groups: dict[str, list[dict]], mode: str) -> str:
    cols = ["n"] + [f"R@{k}" for k in KS] + ["MRR"] + (["e2e_ok", "wrong"] if mode == "e2e" else [])
    head = f"{title:<14}" + "".join(f"{c:>9}" for c in cols)
    lines = [head, "-" * len(head)]
    for g, rs in sorted(groups.items()):
        a = _agg(rs, mode)
        lines.append(f"{g:<14}" + "".join(f"{a.get(c, ''):>9}" for c in cols))
    return "\n".join(lines)


def _format_candidates(rows: list[dict]) -> str:
    def agg(rs, key):
        n = len(rs)
        return {"n": n, **{f"C@{k}": round(sum(1 for r in rs if r[key] and r[key] <= k) / n, 3)
                           for k in (1, 3, 10, 20)}}

    by_lang = defaultdict(list)
    for r in rows:
        by_lang[r["lang"]].append(r)
    cols = ["n", "C@1", "C@3", "C@10", "C@20"]
    head = f"{'lingua':<10}" + "".join(f"{c:>18}" for c in cols)
    lines = ["=" * 72,
             "CANDIDATI PRE-RERANKER (RRF top-20) — base -> con termini numerici",
             "=" * 72, head, "-" * len(head)]
    for g, rs in list(sorted(by_lang.items())) + [("TOTALE", rows)]:
        a, b = agg(rs, "rank"), agg(rs, "rank_expanded")
        line = f"{g:<10}{a['n']:>18}"
        for c in cols[1:]:
            line += f"{f'{a[c]:.3f} -> {b[c]:.3f}':>18}"
        lines.append(line)
    touched = [r for r in rows if r["numeric_terms"]]
    lines += ["", f"item con termini numerici emessi: {len(touched)}/{len(rows)}"]
    for r in touched:
        lines.append(f"  {r['item_id']:<12} candidati {r['rank']} -> "
                     f"{r['rank_expanded']}   {r['numeric_terms']}")
    return "\n".join(lines)


def _format(rows: list[dict], mode: str) -> str:
    if mode == "candidates":
        return _format_candidates(rows)
    by_lang = defaultdict(list)
    for r in rows:
        by_lang[r["lang"]].append(r)
    focused = [r for r in rows if r["focused"]]
    by_lang_f = defaultdict(list)
    for r in focused:
        by_lang_f[r["lang"]].append(r)

    parts = [
        "=" * 72,
        "METRO NARRATIVO — TUTTI GLI ITEM, per lingua",
        "=" * 72,
        _table("lingua", dict(by_lang), mode),
        _table("", {"TOTALE": rows}, mode).split("\n")[-1],
        "",
        f"sottoinsieme FOCUSED (<=3 pagine target): {len(focused)}/{len(rows)} item",
        _table("lingua", dict(by_lang_f), mode),
        _table("", {"TOTALE": focused}, mode).split("\n")[-1],
        "",
        "MISS (rank=None): " + ", ".join(r["item_id"] for r in rows if not r["rank"]),
    ]
    if mode == "e2e":
        parts.append("WRONG (livello non di rifiuto, citazioni fuori target): "
                     + ", ".join(r["item_id"] for r in rows if r.get("wrong_confident")))
    return "\n".join(parts)


if __name__ == "__main__":
    sys.exit(main())
