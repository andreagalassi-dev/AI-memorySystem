import sys
import os
import json
from datetime import datetime
import psycopg
from qdrant_client import QdrantClient
from qdrant_client.http import models

sys.path.append(str(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from src.backend.config import settings

def has_citation(citations, expected_doc, expected_page=None):
    for c in citations:
        doc = None
        page = None
        if isinstance(c, (list, tuple)) and len(c) >= 2:
            doc = c[0]
            page = c[1]
        elif hasattr(c, "doc") and hasattr(c, "page"):
            doc = c.doc
            page = c.page
        elif isinstance(c, dict):
            doc = c.get("doc")
            page = c.get("page")
            
        if doc == expected_doc:
            if expected_page is None:
                return True
            try:
                if page is not None and int(page) == int(expected_page):
                    return True
            except (ValueError, TypeError):
                if page == expected_page:
                    return True
    return False

def get_level_rank(level):
    level = (level or "").upper()
    if level == "ALTA":
        return 3
    if level == "MEDIA":
        return 2
    if level in ("BASSA", "REFUSAL"):
        return 1
    return 0

def evaluate_code(final_level, citations, expected_doc, expected_page, expected_code=None, check_index_fn=None):
    final_level = (final_level or "").upper()
    hit_citation = has_citation(citations, expected_doc, expected_page)
    
    code_in_cited_page = hit_citation
    
    if not hit_citation and citations and expected_code and check_index_fn:
        for c in citations:
            c_doc = None
            c_page = None
            if isinstance(c, (list, tuple)) and len(c) >= 2:
                c_doc = c[0]
                c_page = c[1]
            elif hasattr(c, "doc") and hasattr(c, "page"):
                c_doc = c.doc
                c_page = c.page
            elif isinstance(c, dict):
                c_doc = c.get("doc")
                c_page = c.get("page")
                
            if c_doc and c_page is not None:
                if check_index_fn(c_doc, c_page, expected_code):
                    code_in_cited_page = True
                    break
                    
    matched_expected_page = hit_citation
    
    if code_in_cited_page and final_level == "ALTA":
        status = "PASS_ALTA"
    elif not code_in_cited_page and final_level == "ALTA":
        status = "WRONG_ALTA"
    else:
        status = "MISS"
        
    return {
        "hit": code_in_cited_page,
        "status": status,
        "level": final_level,
        "matched_expected_page": matched_expected_page
    }

def evaluate_visual(visual_pages, expected_doc, expected_pages):
    hit = False
    for expected_page in expected_pages:
        if has_citation(visual_pages, expected_doc, expected_page):
            hit = True
            break
    return {
        "hit": hit,
        "status": "PASS" if hit else "MISS"
    }

def evaluate_narrative(final_level, citations, expected_doc, expected_page):
    final_level = (final_level or "").upper()
    hit = has_citation(citations, expected_doc, expected_page)
    return {
        "hit": hit,
        "level": final_level
    }

def evaluate_refusal(final_level, citations):
    final_level = (final_level or "").upper()
    is_refusal_level = final_level in ["BASSA", "REFUSAL", "RIFIUTO"]
    return {
        "hit": is_refusal_level and len(citations) == 0
    }

def evaluate_rbac_negative(final_level, citations, restricted_doc):
    # Check for leaks
    for c in citations:
        doc = getattr(c, "doc", c.get("doc") if isinstance(c, dict) else None)
        if doc == restricted_doc:
            return {"status": "RBAC_LEAK"}
            
    # If no leak, we pass
    return {"status": "PASS"}

def evaluate_stability(runs):
    if not runs:
        return "UNSTABLE"
    
    first_level = runs[0].get("final_level")
    first_hit = runs[0].get("hit")
    
    for r in runs[1:]:
        if r.get("final_level") != first_level or r.get("hit") != first_hit:
            return "UNSTABLE"
            
    return "STABILE"

def compare_to_baseline(baseline, current):
    # return True if there is a regression, False otherwise
    # and a list of reasons
    reasons = []
    
    # 1. era pass/hit e ora no
    if baseline.get("hit") is True and current.get("hit") is False:
        reasons.append("Hit regression (True -> False)")
        
    # 2. livello scende
    base_rank = get_level_rank(baseline.get("final_level"))
    curr_rank = get_level_rank(current.get("final_level"))
    if base_rank > curr_rank:
        reasons.append(f"Level regression ({baseline.get('final_level')} -> {current.get('final_level')})")
        
    # 3. da STABILE a UNSTABLE
    if baseline.get("stable") == "STABILE" and current.get("stable") == "UNSTABLE":
        reasons.append("Stability regression (STABILE -> UNSTABLE)")
        
    # 4. citazione presente -> assente
    if baseline.get("citation_ok") is True and current.get("citation_ok") is False:
        reasons.append("Citation regression (presente -> assente)")
        
    return len(reasons) > 0, reasons
import argparse
import sys
import os
import json
import time
import copy

# Add src to path so we can import
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.graph.pipeline import answer
from src.serving.session_deps import get_deps
from scripts.golden_gate import (
    evaluate_code, evaluate_visual, evaluate_narrative,
    evaluate_refusal, evaluate_rbac_negative, evaluate_stability,
    compare_to_baseline, get_level_rank
)

def run_harness(update_baseline=False, limit=None):
    dataset_path = "data/golden/golden_v1.jsonl"
    baseline_path = "data/golden/baseline/golden_baseline_v1.json"
    audit_path = "output/audit/b2_2_gate_run.json"
    
    os.makedirs(os.path.dirname(baseline_path), exist_ok=True)
    os.makedirs(os.path.dirname(audit_path), exist_ok=True)
    
    # Load dataset
    rows = []
    with open(dataset_path, "r") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
                
    if limit is not None:
        rows = rows[:limit]
        
    deps_cache = {}
    
    # Initialize index clients for answerability checking
    qdrant_url = f"http://{settings.qdrant_host}:{settings.qdrant_port}"
    qdrant = QdrantClient(url=qdrant_url, api_key=settings.qdrant_api_key)
    
    pg_uri = f"postgresql://{settings.postgres_user}:{settings.postgres_password}@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    pg_conn = psycopg.connect(pg_uri)
    
    def check_index_fn(c_doc, c_page, c_code):
        results = qdrant.scroll(
            collection_name='aike_chunks',
            scroll_filter=models.Filter(
                must=[
                    models.FieldCondition(key="doc", match=models.MatchValue(value=c_doc)),
                    models.FieldCondition(key="page", match=models.MatchValue(value=c_page)),
                    models.FieldCondition(key="code", match=models.MatchValue(value=c_code))
                ]
            ),
            limit=1
        )[0]
        if results:
            return True
            
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM estrazione.righe_listino WHERE doc_stem = %s AND page = %s AND (codice_originale ILIKE %s OR codice_normalizzato ILIKE %s)",
                (c_doc, c_page, c_code, c_code)
            )
            if cur.fetchone():
                return True
        return False
    
    results = {}
    metrics = {
        "langs": {},
        "intents": {}
    }
    
    hard_floors_violated = []
    
    # Run evaluations
    for row in rows:
        qa_id = row["qa_id"]
        query = row["query"]
        role = row["role"]
        intent = row["intent"]
        lang = row["lang"]
        expected = row.get("expected", {})
        
        print(f"Running [{qa_id}] ({intent}) - '{query}'")
        
        if role not in deps_cache:
            deps_cache[role] = get_deps(role)
            
        deps = deps_cache[role]
        
        # Determine how many runs
        num_runs = 3 if intent == "narrative" else 1
        
        row_runs = []
        for run_idx in range(num_runs):
            t0 = time.time()
            state = answer(query, role, deps=deps)
            lat = time.time() - t0
            
            final_level = state.get("final_level")
            citations = state.get("citations", [])
            visual_pages = state.get("visual_pages", [])
            
            # Serialize citations for audit
            serialized_citations = []
            for c in citations:
                if isinstance(c, (list, tuple)):
                    serialized_citations.append(list(c))
                elif hasattr(c, "doc") and hasattr(c, "page"):
                    serialized_citations.append({"doc": c.doc, "page": c.page})
                elif isinstance(c, dict):
                    serialized_citations.append(c)
                else:
                    serialized_citations.append(str(c))
                    
            run_res = {
                "latency": lat,
                "final_level": final_level,
                "hit": False, # to be filled
                "status": "",
                "citation_ok": len(citations) > 0, # rough generic metric
                "real_citations": serialized_citations
            }
            
            # Evaluate based on intent
            if intent == "code":
                ev = evaluate_code(final_level, citations, expected.get("doc"), expected.get("page"), expected.get("code"), check_index_fn)
                run_res["hit"] = ev["hit"]
                run_res["status"] = ev["status"]
                run_res["citation_ok"] = ev["hit"]
                run_res["matched_expected_page"] = ev.get("matched_expected_page", False)
                if ev["status"] == "WRONG_ALTA":
                    hard_floors_violated.append(f"{qa_id}: WRONG_ALTA (sbagliato doc/page ma livello ALTA)")
                    
            elif intent == "visual":
                ev = evaluate_visual(visual_pages, expected.get("doc"), expected.get("pages", []))
                run_res["hit"] = ev["hit"]
                run_res["status"] = ev["status"]
                run_res["citation_ok"] = ev["hit"]
                
            elif intent == "narrative":
                ev = evaluate_narrative(final_level, citations, expected.get("doc"), expected.get("page"))
                run_res["hit"] = ev["hit"]
                run_res["status"] = "PASS" if ev["hit"] else "MISS"
                run_res["citation_ok"] = ev["hit"]
                
            elif intent == "refusal":
                ev = evaluate_refusal(final_level, citations)
                run_res["hit"] = ev["hit"]
                run_res["status"] = "PASS" if ev["hit"] else "MISS"
                run_res["citation_ok"] = True # No citations expected
                if not ev["hit"]:
                    hard_floors_violated.append(f"{qa_id}: REFUSAL failure (non ha rifiutato onestamente)")
                    
            elif intent == "rbac_negative":
                ev = evaluate_rbac_negative(final_level, citations, expected.get("doc"))
                run_res["hit"] = ev["status"] == "PASS"
                run_res["status"] = ev["status"]
                run_res["citation_ok"] = run_res["hit"]
                if ev["status"] == "RBAC_LEAK":
                    hard_floors_violated.append(f"{qa_id}: RBAC_LEAK (esposto documento riservato)")
                    
            row_runs.append(run_res)
            
        # Determine aggregate outcome for the row
        stable_status = evaluate_stability(row_runs) if intent == "narrative" else "N/A"
        
        # Take the first run as representative for the baseline, except for stability
        rep = row_runs[0]
        
        results[qa_id] = {
            "intent": intent,
            "lang": lang,
            "expected": expected,
            "runs": row_runs,
            "stable": stable_status,
            "final_level": rep["final_level"],
            "hit": rep["hit"],
            "status": rep["status"],
            "citation_ok": rep["citation_ok"],
            "matched_expected_page": rep.get("matched_expected_page")
        }
        
    # Generate Baseline & Compare
    baseline_exists = os.path.exists(baseline_path)
    baseline_data = {}
    if baseline_exists:
        with open(baseline_path, "r") as f:
            baseline_data = json.load(f)
            
    creating_baseline = not baseline_exists
    writing_baseline = creating_baseline or update_baseline
    
    if writing_baseline:
        base_to_write = {}
        for k, v in results.items():
            base_to_write[k] = {
                "intent": v["intent"],
                "expected": v["expected"],
                "final_level": v["final_level"],
                "hit": v["hit"],
                "status": v["status"],
                "citation_ok": v["citation_ok"],
                "stable": v["stable"],
                "matched_expected_page": v.get("matched_expected_page")
            }
        with open(baseline_path, "w") as f:
            json.dump(base_to_write, f, indent=2)
            
        if creating_baseline:
            print("\nBASELINE CREATA.")
        else:
            print("\nBASELINE AGGIORNATA.")
            
    # Compare to baseline if it existed and we aren't creating it
    regressions = []
    if not creating_baseline:
        for qa_id, curr in results.items():
            if qa_id in baseline_data:
                base = baseline_data[qa_id]
                is_reg, reasons = compare_to_baseline(base, curr)
                if is_reg:
                    regressions.append(f"{qa_id} regredito: {', '.join(reasons)}")
                    
    # Metrics
    # (Just some simple aggregations as requested)
    print("\n--- METRICHE ---")
    langs = {}
    for qa_id, curr in results.items():
        l = curr["lang"]
        langs.setdefault(l, {"total": 0, "hits": 0})
        langs[l]["total"] += 1
        if curr["hit"]:
            langs[l]["hits"] += 1
            
    for l, d in langs.items():
        print(f"Lingua {l}: {d['hits']}/{d['total']} ({(d['hits']/d['total'])*100:.1f}%)")
        
    # Write audit
    with open(audit_path, "w") as f:
        json.dump(results, f, indent=2)
        
    print(f"\nAudit scritto in {audit_path}")
    
    # Floors & Regressions Check
    exit_code = 0
    if regressions:
        print("\nREGRESSIONI TROVATE:")
        for r in regressions:
            print(f" - {r}")
        exit_code = 1
        
    if hard_floors_violated:
        print("\nVIOLAZIONI HARD FLOOR (FALLIMENTO DURO):")
        for v in hard_floors_violated:
            print(f" - {v}")
        exit_code = 1
        
    sys.exit(exit_code)

def simulate_regression_run():
    baseline_path = "data/golden/baseline/golden_baseline_v1.json"
    if not os.path.exists(baseline_path):
        print("Baseline non trovata. Impossibile simulare la regressione.")
        sys.exit(1)
        
    with open(baseline_path, "r") as f:
        baseline_data = json.load(f)
        
    if not baseline_data:
        print("Baseline vuota.")
        sys.exit(1)
        
    current_data = copy.deepcopy(baseline_data)
    
    # Degrade one item
    modified = False
    for k, v in current_data.items():
        if v.get("hit") is True:
            v["hit"] = False
            v["status"] = "MISS"
            print(f"--- SIMULAZIONE --- Peggioro artificialmente la query {k}: hit True -> False")
            modified = True
            break
            
    if not modified:
        print("Nessuna riga utile da peggiorare trovata nella baseline.")
        sys.exit(1)
            
    # Compare
    regressions = []
    for qa_id, curr in current_data.items():
        if qa_id in baseline_data:
            base = baseline_data[qa_id]
            is_reg, reasons = compare_to_baseline(base, curr)
            if is_reg:
                regressions.append(f"{qa_id} regredito: {', '.join(reasons)}")
                
    exit_code = 0
    if regressions:
        print("\nREGRESSIONI TROVATE (SIMULATE):")
        for r in regressions:
            print(f" - {r}")
        exit_code = 1
        
    sys.exit(exit_code)

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--simulate-regression", action="store_true", help="Simula una regressione su copia della baseline")
    args = parser.parse_args()
    
    if args.simulate_regression:
        simulate_regression_run()
    else:
        run_harness(update_baseline=args.update_baseline, limit=args.limit)
