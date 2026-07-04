"""Debito §2 — A/B Query Expansion: routed_rr (baseline) vs routed_rr_qx.

Misura l'exit proof del piano approvato:
  1. R@1/5/10 + MRR per tipo e per lingua, baseline vs espansa;
  2. regressione su code/mixed = per costruzione zero (stessa via), verificata;
  3. ALTA precision con gli score di PRODUZIONE (sigmoid del cross-encoder,
     come _default_deps del grafo — NON gli score RRF che l'harness P2.4
     riporta): tra i positivi con margine >= tau_hi (0.90), quanti hanno il
     target in top-1? Deve restare 1.0 (zero falsi-ALTA);
  4. margini dei negativi (mai sopra tau_hi con evidenza).

Le query 'code' girano una volta sola (identiche nelle due varianti: scope v1
= solo route natural). Output: output/eval/expansion_ab_v1.json + stdout.

Uso: uv run scripts/evaluate_expansion_ab.py
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.retrieval.confidence import CALIBRATED_V1, compute_margin  # noqa: E402
from src.retrieval.expansion import expand_query  # noqa: E402
from src.retrieval.rerank import RERANK_TOPK, _reranker  # noqa: E402
from src.retrieval.router import classify_query  # noqa: E402
from src.retrieval.search import search  # noqa: E402

EVAL_SET = ROOT / "data" / "eval" / "eval_set_v1.jsonl"
OUT_JSON = ROOT / "output" / "eval" / "expansion_ab_v1.json"
KS = (1, 5, 10)
LIMIT = 10


def crit_match(payload: dict, crit: dict) -> bool:
    if "code" in crit:
        return payload.get("code") == crit["code"]
    return payload.get("doc") == crit["doc"] and payload.get("pos") == crit["pos"]


def sigmoid_rerank(query: str, hits: list) -> list[tuple[float, object]]:
    """Score di produzione: sigmoid del cross-encoder (pipeline._default_deps)."""
    import torch

    if not hits:
        return []
    tok, model, device = _reranker()
    pairs = [(query, h.payload.get("text") or h.payload.get("desc") or "")
             for h in hits]
    batch = tok(pairs, padding=True, truncation=True, max_length=512,
                return_tensors="pt").to(device)
    with torch.no_grad():
        probs = torch.sigmoid(model(**batch).logits.squeeze(-1).float()).cpu().tolist()
    if not isinstance(probs, list):
        probs = [probs]
    return sorted(zip(probs, hits), key=lambda t: -t[0])[:LIMIT]


def run_pair(client, query: str, expanded: bool) -> dict:
    """Una query, una variante -> {hits, scores, expansion_terms, t_expand}."""
    route = classify_query(query)
    terms, t_exp = [], 0.0
    if expanded and route.query_type == "natural":
        t0 = time.time()
        terms = expand_query(query)
        t_exp = time.time() - t0
    if route.query_type == "code":
        hits = search(client, query, "routed", limit=LIMIT)
        return {"route": "code", "ranked": [(h.score, h) for h in hits],
                "terms": terms, "t_expand": t_exp}
    base = search(client, query, "routed", limit=RERANK_TOPK,
                  extra_terms=terms or None)
    return {"route": route.query_type, "ranked": sigmoid_rerank(query, base),
            "terms": terms, "t_expand": t_exp}


def metrics(rows: list[dict], key: str) -> dict:
    groups = defaultdict(list)
    for r in rows:
        groups[r[key]].append(r["rank"])
    out = {}
    for g, ranks in sorted(groups.items()):
        m = {f"R@{k}": round(sum(1 for r in ranks if r and r <= k) / len(ranks), 3)
             for k in KS}
        m["MRR"] = round(sum(1 / r for r in ranks if r) / len(ranks), 3)
        m["n"] = len(ranks)
        out[g] = m
    return out


def main() -> int:
    pairs = [json.loads(line) for line in EVAL_SET.open(encoding="utf-8")]
    client = get_qdrant_client()
    tau_hi = CALIBRATED_V1.tau_hi

    rows = {"baseline": [], "expanded": []}
    alta = {"baseline": [], "expanded": []}       # (qa_id, margin, rank)
    neg_margins = {"baseline": [], "expanded": []}
    expansion_log = {}
    t_expand_all: list[float] = []

    for pair in pairs:
        q, qa = pair["query"], pair["qa_id"]
        route = classify_query(q)
        res_base = run_pair(client, q, expanded=False)
        # code (e mixed: scope v1) -> stessa via: riusa il risultato baseline
        res_exp = (run_pair(client, q, expanded=True)
                   if route.query_type == "natural" else res_base)
        if res_exp["terms"]:
            expansion_log[qa] = res_exp["terms"]
            t_expand_all.append(res_exp["t_expand"])

        for variant, res in (("baseline", res_base), ("expanded", res_exp)):
            scores = [s for s, _ in res["ranked"]]
            margin = compute_margin(scores)
            if pair["query_type"] == "negative":
                has_ev = bool(scores) and (scores[0] > 0 if res["route"] == "code"
                                           else True)
                neg_margins[variant].append(
                    {"qa_id": qa, "margin": round(margin, 4), "has_ev": has_ev})
                continue
            rank = next((i for i, (_, h) in enumerate(res["ranked"], start=1)
                         if any(crit_match(h.payload, c) for c in pair["expected"])),
                        None)
            rows[variant].append({"qa_id": qa, "rank": rank, "lang": pair["lang"],
                                  "query_type": pair["query_type"]})
            if margin >= tau_hi:
                alta[variant].append({"qa_id": qa, "margin": round(margin, 4),
                                      "rank": rank})

    report: dict = {"date": str(date.today()), "eval_set": EVAL_SET.name,
                    "tau_hi": tau_hi,
                    "expansion_terms": expansion_log,
                    "t_expand_mediana_ms": round(
                        sorted(t_expand_all)[len(t_expand_all) // 2] * 1000, 2)
                        if t_expand_all else None}
    for variant in ("baseline", "expanded"):
        a = alta[variant]
        n_ok = sum(1 for x in a if x["rank"] == 1)
        report[variant] = {
            "per_type": metrics(rows[variant], "query_type"),
            "per_lang": metrics(rows[variant], "lang"),
            "alta_margine": {"n": len(a), "top1_corretti": n_ok,
                             "precision": round(n_ok / len(a), 3) if a else None,
                             "casi": a},
            "neg_margine_max_con_evidenza": max(
                (x["margin"] for x in neg_margins[variant] if x["has_ev"]),
                default=None),
        }

    # movimenti per-query (lettura qualitativa)
    base_rank = {r["qa_id"]: r["rank"] for r in rows["baseline"]}
    moved = {r["qa_id"]: f"{base_rank[r['qa_id']]} -> {r['rank']}"
             for r in rows["expanded"] if r["rank"] != base_rank[r["qa_id"]]}
    report["movimenti"] = moved

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print(f"[ab] espansione su {len(expansion_log)} query naturali; "
          f"t_expand mediana={report['t_expand_mediana_ms']}ms")
    for variant in ("baseline", "expanded"):
        pt = report[variant]["per_type"]
        line = "  ".join(f"{t}: R@1={m['R@1']} R@10={m['R@10']} (n={m['n']})"
                         for t, m in pt.items())
        am = report[variant]["alta_margine"]
        print(f"[ab] {variant:8s} {line}")
        print(f"[ab] {'':8s} ALTA(margine>={tau_hi}): n={am['n']} "
              f"precision={am['precision']}  neg_margin_max="
              f"{report[variant]['neg_margine_max_con_evidenza']}")
    print(f"[ab] movimenti: {moved}")
    print(f"[ab] report: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
