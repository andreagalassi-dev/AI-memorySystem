import json
import random
import torch
import sys
import time
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.retrieval.colqwen3 import _model_and_processor, embed_pages, embed_query  # noqa: E402

def main():
    eval_file = ROOT / "data" / "eval" / "eval_visual_v0.jsonl"
    queries = []
    with open(eval_file) as f:
        for line in f:
            if line.strip():
                queries.append(json.loads(line))
                
    # 1. Collect all GT pages
    gt_pages_set = set()
    for q in queries:
        for expected in q["expected"]:
            doc = expected["doc"]
            for p in expected["pages"]:
                gt_pages_set.add((doc, p))
                
    # 2. Collect available distractors
    renders_dir = ROOT / "output" / "visual" / "renders"
    all_pages = []
    for doc_dir in renders_dir.iterdir():
        if doc_dir.is_dir():
            doc = doc_dir.name
            for p_file in doc_dir.glob("p*.png"):
                p_num = int(p_file.stem[1:])
                all_pages.append((doc, p_num))
                
    distractors_pool = [p for p in all_pages if p not in gt_pages_set]
    
    gt_docs = {doc for doc, _ in gt_pages_set}
    hard_distractors = [p for p in distractors_pool if p[0] in gt_docs]
    easy_distractors = [p for p in distractors_pool if p[0] not in gt_docs]
    
    random.seed(42)
    selected_distractors = []
    if len(hard_distractors) >= 10:
        selected_distractors.extend(random.sample(hard_distractors, 10))
        selected_distractors.extend(random.sample(easy_distractors, 5))
    else:
        selected_distractors.extend(hard_distractors)
        selected_distractors.extend(random.sample(easy_distractors, 15 - len(hard_distractors)))
        
    page_pool = list(gt_pages_set) + selected_distractors
    print(f"Total queries: {len(queries)}")
    print(f"Total GT pages: {len(gt_pages_set)}")
    print(f"Total distractors: {len(selected_distractors)}")
    print(f"Total pages to embed: {len(page_pool)}")
    
    # Pre-load model
    model, processor, device = _model_and_processor()
    print(f"Model loaded on device: {device}")
    
    # 3. Embed all pages
    page_embeddings = {}
    for i, (doc, p) in enumerate(page_pool):
        img_path = renders_dir / doc / f"p{p:04d}.png"
        img = Image.open(img_path).convert("RGB")
        t0 = time.time()
        emb = embed_pages([img])[0]
        page_embeddings[(doc, p)] = torch.tensor(emb, dtype=torch.float32)
        print(f"Embedding page {i+1}/{len(page_pool)}: {doc}/p{p} in {time.time()-t0:.2f}s", flush=True)
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
            
    # 4. Evaluate queries
    r1, r3, r5 = 0, 0, 0
    total_margin = 0.0
    
    print("\n--- RESULTS ---", flush=True)
    for q_idx, q in enumerate(queries):
        query_text = q["query"]
        expected_list = []
        for expected in q["expected"]:
            doc = expected["doc"]
            for p in expected["pages"]:
                expected_list.append((doc, p))
                
        q_emb = torch.tensor(embed_query(query_text), dtype=torch.float32) # (N, dim)
        num_tokens = q_emb.shape[0]
        
        scores = []
        for (doc, p), p_emb in page_embeddings.items():
            sims = torch.matmul(q_emb, p_emb.T) # (N, M)
            max_sims = sims.max(dim=1).values # (N,)
            score = max_sims.sum().item()
            scores.append((score, (doc, p)))
            
        scores.sort(key=lambda x: x[0], reverse=True)
        ranked_pages = [page for _, page in scores]
        
        # Calculate ranks
        best_rank = float('inf')
        for exp in expected_list:
            if exp in ranked_pages:
                rank = ranked_pages.index(exp) + 1
                best_rank = min(best_rank, rank)
                
        if best_rank <= 1:
            r1 += 1
        if best_rank <= 3:
            r3 += 1
        if best_rank <= 5:
            r5 += 1
        
        # Calculate margin
        gt_score = max([score for score, page in scores if page in expected_list])
        distractor_scores = [score for score, page in scores if page not in expected_list]
        best_distractor_score = distractor_scores[0] if distractor_scores else 0
        margin_per_token = (gt_score - best_distractor_score) / num_tokens
        total_margin += margin_per_token
        
        print(f"Q{q_idx+1}: {query_text}")
        print(f"  Best Rank: {best_rank}")
        print(f"  GT Score: {gt_score:.2f}, Best Distractor: {best_distractor_score:.2f}")
        print(f"  Margin/Token: {margin_per_token:.4f}")
        for i in range(min(5, len(scores))):
            mark = "*" if scores[i][1] in expected_list else " "
            print(f"  {i+1}. {mark} {scores[i][1][0]}/p{scores[i][1][1]} ({scores[i][0]:.2f})")
        print(flush=True)
        
    print("--- SUMMARY ---")
    print(f"R@1: {r1}/{len(queries)} ({r1/len(queries):.2f})")
    print(f"R@3: {r3}/{len(queries)} ({r3/len(queries):.2f})")
    print(f"R@5: {r5}/{len(queries)} ({r5/len(queries):.2f})")
    print(f"Avg Margin/Token: {total_margin/len(queries):.4f}")

if __name__ == "__main__":
    main()
