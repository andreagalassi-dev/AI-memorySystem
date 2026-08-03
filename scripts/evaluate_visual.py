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
import os
import argparse
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.retrieval.pixelrag.search import visual_search  # noqa: E402
from src.retrieval.pixelrag.index import QdrantVisualIndex  # noqa: E402
from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder  # noqa: E402
from src.common.roles import Role  # noqa: E402
from src.retrieval.qwen3vl import VISUAL_COLLECTION  # noqa: E402

from src.retrieval.pixelrag.reranker import Qwen3VLReranker  # noqa: E402
from src.serving.multiplexer import ModelMultiplexer, FakeModelRuntime  # noqa: E402

EVAL = Path(os.environ.get("EVAL_FILE", ROOT / "data" / "eval" / "eval_visual_v0.jsonl"))
OUT = ROOT / "output/eval/p27_visual_results.json"
KS = (1, 3, 5)

def main() -> int:
    parser = argparse.ArgumentParser(description="Eval del retrieval visivo.")
    parser.add_argument("--rerank", action="store_true", help="Abilita il reranker")
    args = parser.parse_args()

    start_time = time.time()

    # Setup per pixelrag: usiamo Qwen3VLEmbedder DIRETTO per rimanere residente
    visual_embedder = Qwen3VLEmbedder()
    
    if args.rerank:
        mux = ModelMultiplexer(runtime=FakeModelRuntime(), max_resident=2)
        visual_reranker = Qwen3VLReranker(multiplexer=mux)
    else:
        visual_reranker = None
    client = get_qdrant_client()
    visual_index = QdrantVisualIndex(client)
    
    # 1. Carica dataset
    pairs = [json.loads(line) for line in EVAL.open(encoding="utf-8")]

    # 2. Controllo answerability (tutte le pagine attese DEVONO esistere nell'indice visivo)
    indexed_raw, _ = client.scroll(VISUAL_COLLECTION, limit=10000, with_payload=True)
    indexed = {(p.payload["doc"], p.payload["page"]) for p in indexed_raw}
    print(f"[eval-vis] query={len(pairs)} pagine indicizzate={len(indexed)}")

    errors = []
    for pair in pairs:
        for crit in pair["expected"]:
            for page in crit["pages"]:
                if (crit["doc"], page) not in indexed:
                    errors.append(f"{pair['qa_id']}: nessuna pagina attesa di "
                                  f"'{crit['doc']}' (pag. {page}) trovata nell'indice visivo")
    if errors:
        print("\nFALLIMENTO ANSWERABILITY:")
        for e in errors:
            print(f"  - {e}")
        return 1

    # 3. Valutazione (usiamo Role.TECNICO per avere accesso a tutti i cataloghi tecnici)
    rows_before, rows_after = [], []
    langs = []
    
    for pair in pairs:
        expected = {(c["doc"], p) for c in pair["expected"] for p in c["pages"]}
        
        # Path di ricerca
        k_val = 60 if args.rerank else 10
        hits_before = visual_search(
            pair["query"],
            embedder=visual_embedder,
            index=visual_index,
            role=Role.TECNICO,
            k=k_val,
            tile_k=300,
            reranker=None
        )
        
        rank_before = next((i for i, h in enumerate(hits_before, 1) if (h.doc, h.page) in expected), None)
        rows_before.append(rank_before)
        
        if args.rerank:
            hits_after = visual_reranker.rerank(pair["query"], hits_before, top_k=10)
            rank_after = next((i for i, h in enumerate(hits_after, 1) if (h.doc, h.page) in expected), None)
            rows_after.append(rank_after)
            
        langs.append(pair["lang"])

    def calc_metrics(rows):
        n = len(rows)
        if not n:
            return {}
        m = {f"recall@{k}": sum(1 for r in rows if r and r <= k) / n for k in KS}
        m["mrr"] = sum(1/r for r in rows if r) / n
        return m

    final_rows = rows_after if args.rerank else rows_before

    if args.rerank:
        print("\n--- RISULTATI PRIMA DEL RERANKER ---")
        m_before = calc_metrics(rows_before)
        for k, v in m_before.items():
            print(f"  {k}: {v:.3f}")
            
        print("\n--- RISULTATI DOPO IL RERANKER ---")
    else:
        print("\n--- RISULTATI TOTALI (SENZA RERANKER) ---")

    m_final = calc_metrics(final_rows)
    for k, v in m_final.items():
        print(f"  {k}: {v:.3f}")
        
    print("\n--- DETTAGLIO PER LINGUA ---")
    lang_set = sorted(list(set(langs)))
    lang_metrics = {}
    for lang in lang_set:
        lang_rows = [r for r, lang_ in zip(final_rows, langs) if lang_ == lang]
        lm = calc_metrics(lang_rows)
        lang_metrics[lang] = lm
        print(f"[{lang.upper()}] (n={len(lang_rows)}) R@1={lm.get('recall@1',0):.3f} R@3={lm.get('recall@3',0):.3f} R@5={lm.get('recall@5',0):.3f} MRR={lm.get('mrr',0):.3f}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as f:
        json.dump({
            "metrics": m_final,
            "by_lang": lang_metrics,
            "config": {"rerank": args.rerank}
        }, f, indent=2)

    total_time = time.time() - start_time
    print(f"\nTempo totale: {total_time:.1f}s")

    if m_final.get("recall@5", 0) < 0.85:
        print("\nSOGLIA NON RAGGIUNTA: recall@5 < 0.85. Mi fermo.")
        sys.exit(1)
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
