import sys
import os
from pathlib import Path

# Preambolo sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import random
import json
from collections import defaultdict
import datetime
from src.backend.config import settings
from src.backend.clients.postgres_client import get_pg_conn
from src.backend.clients.qdrant_client import get_qdrant_client
from src.retrieval.guardrail import extract_candidates
from src.graph.pipeline import answer
from src.common.roles import Role

def classify_root_cause(
    code: str,
    final_level: str,
    candidates: list[dict],
    guardrail_gate: str,
    raw_answer: str,
) -> str:
    """Classify the root cause of a degraded code query."""
    if final_level == "ALTA":
        return "ALTA"
        
    code_in_candidates = False
    for c in candidates:
        text_to_search = f"{c.get('code', '')} {c.get('text', '')} {c.get('desc', '')}".upper()
        if code.upper() in text_to_search:
            code_in_candidates = True
            break
            
    if not code_in_candidates:
        return "RETRIEVAL_MISS"
        
    if guardrail_gate in ("VERIFY", "BLOCK"):
        return "GENERAZIONE"
        
    if "non lo so" in raw_answer.lower() or "rifiutata" in raw_answer.lower():
        return "FUSIONE_RANKING"
        
    if final_level in ("MEDIA", "BASSA"):
        return "FUSIONE_RANKING"
        
    return "ALTRO_DA_INDAGARE"

def get_document_roles():
    conn = get_pg_conn()
    cur = conn.cursor()
    # Need to join document_roles and documents to get document slug and its role
    cur.execute("""
        SELECT d.slug, dr.role 
        FROM public.documents d 
        JOIN public.document_roles dr ON d.id = dr.document_id
    """)
    rows = cur.fetchall()
    return {row[0]: row[1] for row in rows}

def build_sample():
    qc = get_qdrant_client()
    records, _ = qc.scroll("aike_chunks", limit=10000, with_payload=True)
    
    # We want to stratify: ~40 total codes, >= 8 brands/docs, 3 languages (IT/EN/DE), max 5 per doc.
    # Group chunks by doc and lang
    docs_chunks = defaultdict(list)
    for r in records:
        if not r.payload:
            continue
        doc = r.payload.get("doc")
        if not doc:
            continue
        docs_chunks[doc].append(r)
        
    random.seed(42) # fixed seed for reproducibility
    
    # doc roles
    doc_roles = get_document_roles()
    
    sampled_codes = []
    
    # Try to pick docs that cover 3 languages and at least 8 docs.
    # Actually we can just iterate shuffled docs.
    all_docs = list(docs_chunks.keys())
    random.shuffle(all_docs)
    
    for doc in all_docs:
        if len(sampled_codes) >= 40:
            break
        role = doc_roles.get(doc)
        if not role:
            continue # We need a role to query
            
        chunks = docs_chunks[doc]
        random.shuffle(chunks)
        
        doc_codes = set()
        for r in chunks:
            if len(doc_codes) >= 5:
                break
                
            payload = r.payload
            text = f"{payload.get('text', '')} {payload.get('desc', '')} {payload.get('code', '')}"
            # Extract candidates using existing guardrail logic (src/retrieval/guardrail.py extract_candidates)
            candidates = extract_candidates(text)
            
            # Mamoli duplicates note (from B1.a report): do not repair. If we hit one, that's fine.
            # But let's just pick one candidate from the chunk.
            for c in candidates:
                if c not in doc_codes and len(doc_codes) < 5:
                    doc_codes.add(c)
                    sampled_codes.append({
                        "code": c,
                        "doc": doc,
                        "lang": payload.get("lang"),
                        "role": role
                    })
                    break
                    
    return sampled_codes[:40]

def main():
    print("Building sample...")
    sample = build_sample()
    print(f"Generated sample of {len(sample)} codes.")
    
    # Pre-test run: first 5 codes
    output_data = []
    
    out_dir = Path("output/audit")
    out_dir.mkdir(parents=True, exist_ok=True)
    
    start_time = datetime.datetime.now()
    
    for i, s in enumerate(sample):
        print(f"[{i+1}/{len(sample)}] Querying '{s['code']}' (Doc: {s['doc']}, Lang: {s['lang']}) with role '{s['role']}'...")
        query = f"codice {s['code']}"
        res = answer(query, s["role"])
        
        final_level = res.get("final_level", "BASSA")
        raw_answer = res.get("answer", "")
        guardrail_gate = res.get("guardrail_gate", "PASS")
        evidence = res.get("evidence", [])
        
        candidates_retrieval = [
            {
                "doc": e.doc,
                "page": e.page,
                "score": e.score,
                "code": e.code,
                "text": e.text,
                "desc": e.desc
            } for e in evidence
        ]
        
        root_cause = classify_root_cause(s["code"], final_level, candidates_retrieval, guardrail_gate, raw_answer)
        
        output_data.append({
            "sampled_info": s,
            "query": query,
            "role": s["role"],
            "final_level": final_level,
            "fonti": res.get("citations", []),
            "candidati_retrieval": [
                {"doc": c["doc"], "page": c["page"], "score": c["score"]} for c in candidates_retrieval
            ],
            "esito_guardrail": guardrail_gate,
            "testo_grezzo": raw_answer,
            "root_cause": root_cause
        })
        
        with open(out_dir / "b1b_degraded_codes.json", "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
            
    print(f"Audit completed in {datetime.datetime.now() - start_time}. Data saved.")

if __name__ == "__main__":
    main()
