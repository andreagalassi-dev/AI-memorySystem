"""Eval del retrieval visivo ColQwen3 (recall@k pagina-level su aike_pages_visual_colqwen3).

Valuta 4 configurazioni:
(a) ColQwen3 puro
(b) + espansione IT->EN
(c) + reranker
(d) espansione + reranker
"""

from __future__ import annotations

import json
import sys
import os
import psutil
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.qdrant_client import get_qdrant_client
from src.retrieval.colqwen3 import VISUAL_COLLECTION_COLQWEN3, search_visual_colqwen3, embed_query
from src.retrieval.pixelrag.index import PageHit
from src.retrieval.pixelrag.reranker import Qwen3VLReranker
from src.serving.multiplexer import ModelMultiplexer, FakeModelRuntime
from src.retrieval.expansion import expand_query

EVAL = Path(os.environ.get("EVAL_FILE", ROOT / "data" / "eval" / "eval_visual_v0.jsonl"))
OUT = ROOT / "output" / "eval" / "p27_colqwen3_results.json"
TEMP_CANDIDATES = ROOT / "output" / "eval" / "temp_candidates.json"
KS = (1, 3, 5)

mux = ModelMultiplexer(runtime=FakeModelRuntime(), max_resident=2)
visual_reranker = Qwen3VLReranker(multiplexer=mux)

def print_mem(phase_name):
    process = psutil.Process(os.getpid())
    print(f"[{phase_name}] RSS: {process.memory_info().rss / 1024 / 1024:.2f} MB")

def main() -> int:
    client = get_qdrant_client()
    
    pairs = [json.loads(line) for line in EVAL.open(encoding="utf-8")]

    indexed_raw, _ = client.scroll(VISUAL_COLLECTION_COLQWEN3, limit=10000, with_payload=True)
    indexed = {(p.payload["doc"], p.payload["page"]) for p in indexed_raw}
    print(f"[eval-colqwen3] query={len(pairs)} pagine indicizzate={len(indexed)}")

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

    results = {"a": [], "b": [], "c": [], "d": []}
    langs = []
    
    print_mem("INIZIO FASE A (Retrieval)")
    # --- PHASE 1: Retrieval with ColQwen3 ---
    # To avoid loading both 4B and 2B models in RAM simultaneously and causing heavy swapping,
    # we first do all retrievals.
    retrieval_data = []
    
    for pair in pairs:
        query = pair["query"]
        expected = {(c["doc"], p) for c in pair["expected"] for p in c["pages"]}
        langs.append(pair["lang"])
        
        en_terms = expand_query(query, target_lang="EN")
        en_query = query + " " + " ".join(en_terms) if en_terms else None
        
        # Base retrievals (uses ColQwen3-4B)
        hits_it_raw = search_visual_colqwen3(client, query, limit=60)
        hits_it = [PageHit(doc=p.payload["doc"], page=p.payload["page"], score=p.score, n_tiles=1) for p in hits_it_raw]
        
        hits_en = []
        if en_query:
            hits_en_raw = search_visual_colqwen3(client, en_query, limit=60)
            hits_en = [PageHit(doc=p.payload["doc"], page=p.payload["page"], score=p.score, n_tiles=1) for p in hits_en_raw]
            
        # (a) Puro IT
        a_hits = hits_it[:10]
        
        # (b) Espansione IT->EN
        merged = {}
        for h in hits_it + hits_en:
            k = (h.doc, h.page)
            if k not in merged or h.score > merged[k].score:
                merged[k] = h
        b_hits_all = sorted(merged.values(), key=lambda x: x.score, reverse=True)
        b_hits = b_hits_all[:10]
        
        retrieval_data.append({
            "query": query,
            "expected": list(expected),
            "hits_it": [h.__dict__ for h in hits_it],
            "b_hits_all": [h.__dict__ for h in b_hits_all],
            "a_hits": [h.__dict__ for h in a_hits],
            "b_hits": [h.__dict__ for h in b_hits]
        })

    TEMP_CANDIDATES.parent.mkdir(parents=True, exist_ok=True)
    TEMP_CANDIDATES.write_text(json.dumps(retrieval_data, indent=2), encoding="utf-8")
    print_mem("FINE FASE A (Retrieval)")

    # --- MEMORY CLEANUP ---
    from src.retrieval.colqwen3 import _model_and_processor
    _model_and_processor.cache_clear()
    import gc; gc.collect()
    import torch; torch.mps.empty_cache() if torch.backends.mps.is_available() else None
    print_mem("DOPO CLEANUP")
    
    # --- PHASE 2: Reranking ---
    print_mem("INIZIO FASE B (Reranking)")
    retrieval_data = json.loads(TEMP_CANDIDATES.read_text(encoding="utf-8"))

    for data in retrieval_data:
        query = data["query"]
        expected = {tuple(x) for x in data["expected"]}
        hits_it = [PageHit(**h) for h in data["hits_it"]]
        b_hits_all = [PageHit(**h) for h in data["b_hits_all"]]
        a_hits = [PageHit(**h) for h in data["a_hits"]]
        b_hits = [PageHit(**h) for h in data["b_hits"]]

        # (c) Puro IT + Reranker (uses Qwen3VLReranker-2B)
        c_hits = visual_reranker.rerank(query, hits_it, top_k=10)
        
        # (d) Espansione + Reranker
        # Usa la query originale IT per il reranker, ma applicata ai candidati espansi
        d_hits = visual_reranker.rerank(query, b_hits_all[:60], top_k=10)
        
        # Calc ranks
        rank_a = next((i for i, h in enumerate(a_hits, 1) if (h.doc, h.page) in expected), None)
        rank_b = next((i for i, h in enumerate(b_hits, 1) if (h.doc, h.page) in expected), None)
        rank_c = next((i for i, h in enumerate(c_hits, 1) if (h.doc, h.page) in expected), None)
        rank_d = next((i for i, h in enumerate(d_hits, 1) if (h.doc, h.page) in expected), None)
        
        results["a"].append(rank_a)
        results["b"].append(rank_b)
        results["c"].append(rank_c)
        results["d"].append(rank_d)
    print_mem("FINE FASE B (Reranking)")

    def print_metrics(name, rows):
        n = len(rows)
        if not n: return
        mrr = sum(1/r for r in rows if r) / n
        recalls = [sum(1 for r in rows if r and r <= k) / n for k in KS]
        print(f"  {name:15s} | MRR: {mrr:.3f} | R@1: {recalls[0]:.3f} | R@3: {recalls[1]:.3f} | R@5: {recalls[2]:.3f}")

    def eval_group(mask_fn, title):
        print(f"\n=== {title} ===")
        for conf, name in [("a", "Puro"), ("b", "+Espansione"), ("c", "+Reranker"), ("d", "+Entrambi")]:
            filtered_rows = [r for r, l in zip(results[conf], langs) if mask_fn(l)]
            print_metrics(name, filtered_rows)

    eval_group(lambda _: True, "TOTALE")
    eval_group(lambda l: l == "IT", "IT")
    eval_group(lambda l: l == "EN", "EN")
    
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(results, indent=2), encoding="utf-8")

    return 0

if __name__ == "__main__":
    sys.exit(main())
