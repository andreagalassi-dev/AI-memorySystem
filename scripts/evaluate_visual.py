"""P2.7 — Eval del retrieval visivo (recall@k pagina-level su aike_pages_visual).

Fasi fail-fast:
  1. carica eval_visual_v0.jsonl + tutti i payload della collection visiva;
  2. answerability: ogni (doc, pagina) attesa deve esistere nella collection;
  3. per query: top-10 MaxSim -> recall@1/3/5 + MRR (hit = (doc,page) in expected);
  4. scrive output/eval/p27_visual_results.json e stampa la tabella.

Uso: uv run scripts/evaluate_visual.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.retrieval.visual import VISUAL_COLLECTION, search_visual  # noqa: E402

EVAL = ROOT / "data" / "eval" / "eval_visual_v0.jsonl"
OUT = ROOT / "output" / "eval" / "p27_visual_results.json"
KS = (1, 3, 5)


def main() -> int:
    pairs = [json.loads(line) for line in EVAL.open(encoding="utf-8")]
    client = get_qdrant_client()
    indexed: set = set()
    offset = None
    while True:
        pts, offset = client.scroll(VISUAL_COLLECTION, limit=512, offset=offset,
                                    with_payload=True, with_vectors=False)
        indexed.update((p.payload["doc"], p.payload["page"]) for p in pts)
        if offset is None:
            break
    print(f"[eval-vis] query={len(pairs)} pagine indicizzate={len(indexed)}")

    errors = []
    for pair in pairs:
        for crit in pair["expected"]:
            missing = [p for p in crit["pages"] if (crit["doc"], p) not in indexed]
            if len(missing) == len(crit["pages"]):
                errors.append(f"{pair['qa_id']}: nessuna pagina attesa di "
                              f"{crit['doc']} e' indicizzata ({missing})")
    if errors:
        print("[eval-vis] ANSWERABILITY FALLITA:")
        for e in errors:
            print("  -", e)
        return 1

    rows, details = [], []
    for pair in pairs:
        expected = {(c["doc"], p) for c in pair["expected"] for p in c["pages"]}
        hits = search_visual(client, pair["query"], limit=10)
        rank = next((i for i, h in enumerate(hits, 1)
                     if (h.payload["doc"], h.payload["page"]) in expected), None)
        rows.append(rank)
        details.append({"qa_id": pair["qa_id"], "lang": pair["lang"],
                        "rank": rank,
                        "top3": [(h.payload["doc"], h.payload["page"],
                                  round(h.score, 2)) for h in hits[:3]]})

    n = len(rows)
    metrics = {f"recall@{k}": round(sum(1 for r in rows if r and r <= k) / n, 3)
               for k in KS}
    metrics["MRR"] = round(sum(1 / r for r in rows if r) / n, 3)
    metrics["n"] = n
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"metrics": metrics, "details": details},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[eval-vis] {metrics}")
    misses = [d["qa_id"] for d in details if d["rank"] is None]
    print(f"[eval-vis] misses top-10: {misses or 'nessuno'}")
    print(f"[eval-vis] dettagli: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
