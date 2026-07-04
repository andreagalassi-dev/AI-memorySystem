"""Debito §3 — Ri-analisi margini su eval v1: raw vs group-by codice.

Exit proof del piano approvato, con retrieval di PRODUZIONE (espansione §2
sulle naturali, sigmoid cross-encoder su non-code, score raw su code):
  1. quante query BLOCCATE per margine ~0 da duplicati si sbloccano
     (BASSA -> MEDIA/ALTA), e se lo sblocco e' CORRETTO (target top-1);
  2. ALTA precision (margine >= tau_hi -> top-1 giusto) DEVE restare 1.0;
  3. negativi: max margine con evidenza sotto tau_hi (mai falsi-ALTA).
Include il caso-demo 0USBX4 (duplicato bilingue reale) fuori dalle metriche.

Uso: uv run scripts/evaluate_margin_groupby.py
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.retrieval.confidence import (  # noqa: E402
    CALIBRATED_V1,
    Signals,
    compute_margin,
    confidence_level,
    group_scores,
)
from src.retrieval.expansion import expand_query  # noqa: E402
from src.retrieval.rerank import RERANK_TOPK, _reranker  # noqa: E402
from src.retrieval.router import classify_query  # noqa: E402
from src.retrieval.search import search  # noqa: E402

EVAL_SET = ROOT / "data" / "eval" / "eval_set_v1.jsonl"
OUT_JSON = ROOT / "output" / "eval" / "margin_groupby_v1.json"
DEMO_QUERY = "0USBX4"


def crit_match(payload: dict, crit: dict) -> bool:
    if "code" in crit:
        return payload.get("code") == crit["code"]
    return payload.get("doc") == crit["doc"] and payload.get("pos") == crit["pos"]


def sigmoid_rerank(query: str, hits: list) -> list[tuple[float, object]]:
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
    return sorted(zip(probs, hits), key=lambda t: -t[0])[:10]


def retrieve_production(client, query: str) -> tuple[str, list[tuple[float, object]]]:
    """Route + ranked (score, hit) come _default_deps del grafo."""
    route = classify_query(query)
    if route.query_type == "code":
        hits = search(client, query, "routed", limit=10)
        return "code", [(h.score, h) for h in hits]
    terms = expand_query(query) if route.query_type == "natural" else None
    base = search(client, query, "routed", limit=RERANK_TOPK,
                  extra_terms=terms or None)
    return route.query_type, sigmoid_rerank(query, base)


def levels_for(ranked: list[tuple[float, object]], route: str) -> dict:
    """Segnali/livelli con margine RAW e GROUPED sugli stessi hit."""
    scores = [s for s, _ in ranked]
    keyed = [(h.payload.get("code"), s) for s, h in ranked]
    top = ranked[0][1] if ranked else None
    has_ev = bool(ranked) and (scores[0] > 0 if route == "code" else True)
    pl = (getattr(top, "payload", None) or {}) if top else {}
    unreliable = (pl.get("status") == "degraded"
                  or "ocr_source" in (pl.get("warnings") or []))
    out = {}
    for tag, margin in (("raw", compute_margin(scores)),
                        ("grouped", compute_margin(group_scores(keyed)))):
        rep = confidence_level(Signals("PASS", has_ev, margin, unreliable, None),
                               CALIBRATED_V1)
        out[tag] = {"margin": round(margin, 4), "level": rep.level.value}
    return out


def main() -> int:
    pairs = [json.loads(line) for line in EVAL_SET.open(encoding="utf-8")]
    client = get_qdrant_client()
    tau_hi = CALIBRATED_V1.tau_hi

    unlocked, alta = [], {"raw": [], "grouped": []}
    neg_max = {"raw": 0.0, "grouped": 0.0}
    changed = 0
    for pair in pairs:
        route, ranked = retrieve_production(client, pair["query"])
        lv = levels_for(ranked, route)
        if pair["query_type"] == "negative":
            if ranked and (ranked[0][0] > 0 if route == "code" else True):
                for tag in ("raw", "grouped"):
                    neg_max[tag] = max(neg_max[tag], lv[tag]["margin"])
            continue
        rank = next((i for i, (_, h) in enumerate(ranked, start=1)
                     if any(crit_match(h.payload, c) for c in pair["expected"])),
                    None)
        if lv["raw"]["margin"] != lv["grouped"]["margin"]:
            changed += 1
        for tag in ("raw", "grouped"):
            if lv[tag]["margin"] >= tau_hi:
                alta[tag].append({"qa_id": pair["qa_id"], "rank": rank,
                                  "margin": lv[tag]["margin"]})
        if lv["raw"]["level"] == "BASSA" and lv["grouped"]["level"] != "BASSA":
            unlocked.append({"qa_id": pair["qa_id"], "type": pair["query_type"],
                             "lang": pair["lang"], "rank": rank,
                             "da": lv["raw"], "a": lv["grouped"]})

    # caso-demo reale fuori metriche: duplicato bilingue 0USBX4
    d_route, d_ranked = retrieve_production(client, DEMO_QUERY)
    demo = {"query": DEMO_QUERY, "route": d_route,
            "hits": [(round(s, 3), h.payload.get("code"),
                      (h.payload.get("desc") or "")[:30]) for s, h in d_ranked],
            **levels_for(d_ranked, d_route)}

    report = {"date": str(date.today()), "eval_set": EVAL_SET.name,
              "tau_hi": tau_hi, "margini_cambiati": changed,
              "sbloccate": unlocked, "demo_0USBX4": demo,
              "neg_margin_max": neg_max}
    for tag in ("raw", "grouped"):
        a = alta[tag]
        ok = sum(1 for x in a if x["rank"] == 1)
        report[f"alta_{tag}"] = {"n": len(a), "top1_corretti": ok,
                                 "precision": round(ok / len(a), 3) if a else None,
                                 "casi": a}

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, indent=2, ensure_ascii=False),
                        encoding="utf-8")

    print(f"[m3] margini cambiati dal group-by: {changed} query positive")
    print(f"[m3] SBLOCCATE (BASSA->non-BASSA): {len(unlocked)}")
    for u in unlocked:
        print(f"     {u['qa_id']} {u['type']}/{u['lang']} rank={u['rank']} "
              f"margine {u['da']['margin']} -> {u['a']['margin']} "
              f"({u['da']['level']} -> {u['a']['level']})")
    for tag in ("raw", "grouped"):
        a = report[f"alta_{tag}"]
        print(f"[m3] ALTA {tag:7s}: n={a['n']} precision={a['precision']}")
    print(f"[m3] negativi margine max: {report['neg_margin_max']} "
          f"(tau_hi={tau_hi})")
    print(f"[m3] demo {DEMO_QUERY}: margine {demo['raw']['margin']}"
          f" -> {demo['grouped']['margin']} | livello {demo['raw']['level']}"
          f" -> {demo['grouped']['level']} | hits={demo['hits']}")
    print(f"[m3] report: {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
