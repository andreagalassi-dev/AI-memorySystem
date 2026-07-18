"""P3.3 §5 — Calibrazione di delta (accordo testo↔visione) col gate ALTA=1.0.

Cross-probe PIGRO: interroga l'indice visivo (Qwen3-VL) SOLO sulle query che
P3.2 mette in banda MEDIA (le uniche promuovibili). Per delta in {0,1,2} conta
promozioni buone (POS_HIT1) vs cattive (POS_MISS/NEG) e la ALTA precision
risultante. Sceglie il delta piu' grande <=2 con ZERO promozioni cattive.

Uso: EVAL_SET=data/eval/eval_set_v1.jsonl uv run scripts/calibrate_fusion.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
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
)
from src.retrieval.fusion import hit_docpage, page_agreement  # noqa: E402
from src.retrieval.rerank import RERANK_TOPK, _reranker  # noqa: E402
from src.retrieval.router import classify_query  # noqa: E402
from src.retrieval.search import search  # noqa: E402
from src.retrieval.qwen3vl import search_visual  # noqa: E402

EVAL_SET = Path(os.environ.get("EVAL_SET", ROOT / "data" / "eval" / "eval_set_v1.jsonl"))
OUT = ROOT / "output" / "eval" / "fusion_calibration_v1.json"
DELTAS = (0, 1, 2)
TOPK = 3


def crit_match(payload: dict, crit: dict) -> bool:
    if "code" in crit:
        return payload.get("code") == crit["code"]
    return payload.get("doc") == crit["doc"] and payload.get("pos") == crit["pos"]


def first_relevant_rank(hits, expected) -> int | None:
    for rank, h in enumerate(hits, start=1):
        if any(crit_match(h.payload, c) for c in expected):
            return rank
    return None


def reranker_order(query: str, hits: list):
    import torch

    if not hits:
        return [], []
    tok, model, device = _reranker()
    pairs = [(query, h.payload.get("text") or h.payload.get("desc") or "") for h in hits]
    batch = tok(pairs, padding=True, truncation=True, max_length=512,
                return_tensors="pt").to(device)
    with torch.no_grad():
        probs = torch.sigmoid(model(**batch).logits.squeeze(-1).float()).cpu().tolist()
    if not isinstance(probs, list):
        probs = [probs]
    ranked = sorted(zip(probs, hits), key=lambda t: -t[0])
    return [s for s, _ in ranked], [h for _, h in ranked]


def collect() -> list[dict]:
    client = get_qdrant_client()
    pairs = [json.loads(line) for line in EVAL_SET.open(encoding="utf-8")]
    rows: list[dict] = []
    for p in pairs:
        route = classify_query(p["query"])
        is_neg = p["query_type"] == "negative"
        if route.query_type == "code":
            hits = search(client, p["query"], "routed", limit=10)
            scores, ordered = [h.score for h in hits], hits
            has_ev = bool(hits) and hits[0].score > 0
        else:
            base = search(client, p["query"], "routed", limit=RERANK_TOPK)
            scores, ordered = reranker_order(p["query"], base)
            has_ev = bool(base)
        margin = compute_margin(scores)
        top = ordered[0].payload if ordered else {}
        unreliable = (top.get("status") == "degraded"
                      or "ocr_source" in (top.get("warnings") or []))
        base_level = confidence_level(
            Signals("PASS", has_ev, margin, unreliable, None), CALIBRATED_V1).level.value
        rank = None if is_neg else first_relevant_rank(ordered, p["expected"])
        cat = "NEG" if is_neg else ("POS_HIT1" if rank == 1 else "POS_MISS")

        # cross-probe PIGRO: solo sui MEDIA (gli unici promuovibili)
        visual_dp: list = []
        if base_level == "MEDIA":
            vhits = search_visual(client, p["query"], limit=5)
            visual_dp = [dp for dp in (hit_docpage(h) for h in vhits) if dp]

        rows.append({"qa_id": p["qa_id"], "lang": p["lang"], "cat": cat,
                     "base_level": base_level, "margin": round(margin, 4),
                     "text_top": hit_docpage(ordered[0]) if ordered else None,
                     "visual_dp": visual_dp})
    return rows


def evaluate_delta(rows: list[dict], delta: int) -> dict:
    base_alta = [r for r in rows if r["base_level"] == "ALTA"]
    base_alta_good = sum(1 for r in base_alta if r["cat"] == "POS_HIT1")
    promoted = []
    for r in rows:
        if r["base_level"] != "MEDIA" or not r["text_top"]:
            continue
        tt = tuple(r["text_top"])
        vis = [tuple(x) for x in r["visual_dp"]]
        if page_agreement(tt, vis, delta=delta, topk=TOPK) == "agree":
            promoted.append(r)
    good = sum(1 for r in promoted if r["cat"] == "POS_HIT1")
    bad = len(promoted) - good
    new_alta = len(base_alta) + len(promoted)
    new_good = base_alta_good + good
    by_lang = defaultdict(lambda: [0, 0])
    for r in promoted:
        by_lang[r["lang"]][0 if r["cat"] == "POS_HIT1" else 1] += 1
    return {
        "delta": delta,
        "promozioni": len(promoted), "buone": good, "cattive": bad,
        "promossi": [(r["qa_id"], r["cat"], r["text_top"]) for r in promoted],
        "alta_precision": round(new_good / new_alta, 3) if new_alta else None,
        "alta_n": new_alta,
        "per_lang": {k: {"buone": v[0], "cattive": v[1]} for k, v in sorted(by_lang.items())},
    }


def main() -> int:
    print(f"[fusion] eval set: {EVAL_SET.name}")
    rows = collect()
    n_media = sum(1 for r in rows if r["base_level"] == "MEDIA")
    print(f"[fusion] query={len(rows)}  MEDIA promuovibili (cross-probe)={n_media}")
    results = [evaluate_delta(rows, d) for d in DELTAS]
    for e in results:
        print(f"  delta={e['delta']}: promozioni={e['promozioni']} "
              f"(buone={e['buone']} CATTIVE={e['cattive']}) "
              f"ALTA precision={e['alta_precision']} (n={e['alta_n']})  "
              f"per_lang={e['per_lang']}")
    safe = [e for e in results if e["cattive"] == 0]
    chosen = max(safe, key=lambda e: (e["buone"], e["delta"])) if safe else None
    print(f"[fusion] delta scelto (0 cattive, max buone, <=2): "
          f"{chosen['delta'] if chosen else 'NESSUNO -> promozione non adottata'}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "date": str(date.today()), "eval_set": EVAL_SET.name, "topk": TOPK,
        "delta_scelto": chosen["delta"] if chosen else None,
        "results": results, "rows": rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[fusion] scritto: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
