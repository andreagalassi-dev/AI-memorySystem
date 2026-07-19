"""Script di audit per il ramo visivo del grafo (B1.d)"""
import json
import sys
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.postgres_client import get_pg_conn_readonly
from src.backend.clients.qdrant_client import get_qdrant_client
from src.retrieval.qwen3vl import VISUAL_COLLECTION
from src.serving.session_deps import get_deps
from src.graph.pipeline import answer

def calc_metrics(ranks: list[int | None], ks: tuple[int, ...] = (1, 3, 5)) -> dict[str, float]:
    n = len(ranks)
    if not n:
        return {}
    m = {f"recall@{k}": sum(1 for r in ranks if r and r <= k) / n for k in ks}
    m["mrr"] = sum(1/r for r in ranks if r) / n
    return m

def get_rank(candidates: list[tuple[str, int]], expected: list[tuple[str, int]]) -> int | None:
    for i, c in enumerate(candidates, 1):
        if c in expected:
            return i
    return None

def main():
    eval_file = ROOT / "data" / "eval" / "eval_visual_v0.jsonl"
    out_file = ROOT / "output" / "audit" / "b1d_visual_graph_eval.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    with open(eval_file, "r", encoding="utf-8") as f:
        pairs = [json.loads(line) for line in f]

    # Qdrant: get all indexed pages to check answerability
    client = get_qdrant_client()
    indexed_raw, _ = client.scroll(VISUAL_COLLECTION, limit=10000, with_payload=True)
    indexed = {(p.payload.get("doc"), p.payload.get("page")) for p in indexed_raw if p.payload}
    print(f"[eval-vis] query={len(pairs)} pagine indicizzate={len(indexed)}")

    # Postgres: fetch roles for the expected docs
    doc_roles = {}
    from src.backend.clients.postgres_client import get_pg_conn
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT d.slug, r.role FROM public.documents d JOIN public.document_roles r ON d.id = r.document_id")
            for row in cur.fetchall():
                doc_roles[row[0]] = row[1]

    results = []
    ranks = []
    langs = []

    for pair in pairs:
        query = pair["query"]
        lang = pair["lang"]
        qa_id = pair["qa_id"]
        
        expected = {(c["doc"], p) for c in pair["expected"] for p in c["pages"]}
        
        # Check Answerability
        answerable = True
        missing = []
        for doc, page in expected:
            if (doc, page) not in indexed:
                answerable = False
                missing.append((doc, page))
        
        if not answerable:
            print(f"[!] {qa_id} NON_VALUTABILE: pagine mancanti {missing}")
            results.append({
                "qa_id": qa_id,
                "status": "NON_VALUTABILE",
                "missing_pages": missing
            })
            continue

        # Get role based on expected docs. We assume all expected docs for a query have the same role
        # or at least the query implies a single role.
        expected_doc = pair["expected"][0]["doc"]
        role = doc_roles.get(expected_doc)
        if not role:
            print(f"[!] {qa_id} NON_VALUTABILE: ruolo non trovato per doc '{expected_doc}'")
            results.append({
                "qa_id": qa_id,
                "status": "NON_VALUTABILE",
                "reason": f"Role missing for doc {expected_doc}"
            })
            continue

        print(f"[{qa_id}] Executing query: '{query}' (role: {role})")
        deps = get_deps(role)
        
        t0 = time.perf_counter()
        # Usa il GRAFO tramite answer() - bypassiamo il reranker se il grafo usa route visual (PixelRAG base)
        state = answer(query, role, deps=deps)
        t1 = time.perf_counter()
        latency = t1 - t0

        visual_pages = state.get("visual_pages", [])
        candidates = [(p.doc, p.page) for p in visual_pages]
        
        rank = get_rank(candidates, expected)
        ranks.append(rank)
        langs.append(lang)

        # Hit@1,3,5
        hit_at = {k: bool(rank and rank <= k) for k in (1, 3, 5)}

        results.append({
            "qa_id": qa_id,
            "query": query,
            "role": role,
            "latency": latency,
            "candidates": candidates,
            "expected": list(expected),
            "rank": rank,
            "hit_at": hit_at,
            "status": "EVALUATED"
        })

    # Metrics
    m_all = calc_metrics(ranks)
    latencies = [r["latency"] for r in results if r.get("status") == "EVALUATED"]
    p50_latency = sorted(latencies)[len(latencies)//2] if latencies else 0

    print("\n--- RISULTATI RAMO VISUAL DEL GRAFO ---")
    for k, v in m_all.items():
        print(f"  {k}: {v:.3f}")
    print(f"  latency_p50: {p50_latency:.3f}s")
    
    print("\n--- DETTAGLIO PER LINGUA ---")
    lang_set = sorted(list(set(langs)))
    for l in lang_set:
        l_ranks = [r for r, lang_ in zip(ranks, langs) if lang_ == l]
        lm = calc_metrics(l_ranks)
        print(f"[{l}] (n={len(l_ranks)}) R@5={lm.get('recall@5',0):.3f} MRR={lm.get('mrr',0):.3f}")

    # Salva raw
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump({
            "metrics_overall": m_all,
            "latency_p50": p50_latency,
            "results": results
        }, f, indent=2)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
