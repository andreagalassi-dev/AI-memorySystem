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
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.retrieval.pixelrag.search import visual_search
from src.retrieval.pixelrag.index import QdrantVisualIndex
from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder
from src.common.roles import Role
from src.retrieval.qwen3vl import VISUAL_COLLECTION

from src.retrieval.pixelrag.reranker import Qwen3VLReranker
from src.serving.multiplexer import ModelMultiplexer, FakeModelRuntime

EVAL = Path(os.environ.get("EVAL_FILE", ROOT / "data" / "eval" / "eval_visual_v0.jsonl"))
OUT = ROOT / "output/eval/p27_visual_results.json"
KS = (1, 3, 5)

# Setup per pixelrag: usiamo FakeModelRuntime in eval visivo poiche' i test girano isolati.
# Impostiamo max_resident=2 affinche' embedder e reranker coesistano in RAM senza evincersi
# a vicenda a ogni query (causando thrashing dei pesi HF).
mux = ModelMultiplexer(runtime=FakeModelRuntime(), max_resident=2)
visual_embedder = Qwen3VLEmbedder(multiplexer=mux)
visual_reranker = Qwen3VLReranker(multiplexer=mux)

def main() -> int:
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
        
        # Prima del reranker (60 candidate pages da 300 tile)
        hits_before = visual_search(
            pair["query"],
            embedder=visual_embedder,
            index=visual_index,
            role=Role.TECNICO,
            k=60,
            tile_k=300,
            reranker=None
        )
        
        # Dopo il reranker (top 10)
        hits_after = visual_reranker.rerank(pair["query"], hits_before, top_k=10)
        
        rank_before = next((i for i, h in enumerate(hits_before, 1) if (h.doc, h.page) in expected), None)
        rank_after = next((i for i, h in enumerate(hits_after, 1) if (h.doc, h.page) in expected), None)
        
        rows_before.append(rank_before)
        rows_after.append(rank_after)
        langs.append(pair["lang"])

    def calc_metrics(rows):
        n = len(rows)
        if not n: return {}
        m = {f"recall@{k}": sum(1 for r in rows if r and r <= k) / n for k in KS}
        m["mrr"] = sum(1/r for r in rows if r) / n
        return m

    print("\n--- RISULTATI PRIMA DEL RERANKER ---")
    m_before = calc_metrics(rows_before)
    for k, v in m_before.items():
        print(f"  {k}: {v:.3f}")
        
    print("\n--- RISULTATI DOPO IL RERANKER ---")
    m_after = calc_metrics(rows_after)
    for k, v in m_after.items():
        print(f"  {k}: {v:.3f}")
        
    print("\n--- DETTAGLIO PER LINGUA (DOPO RERANKER) ---")
    lang_set = sorted(list(set(langs)))
    for lang in lang_set:
        lang_rows = [r for r, l in zip(rows_after, langs) if l == lang]
        lm = calc_metrics(lang_rows)
        print(f"[{lang.upper()}] (n={len(lang_rows)}) R@5={lm.get('recall@5',0):.3f} MRR={lm.get('mrr',0):.3f}")

    if m_after.get("recall@5", 0) < 0.85:
        print("\nSOGLIA NON RAGGIUNTA: recall@5 < 0.85. Mi fermo.")
        sys.exit(1)
        
    return 0


if __name__ == "__main__":
    sys.exit(main())
