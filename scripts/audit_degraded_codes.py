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
    
    # Scroll paginato
    records = []
    offset = None
    while True:
        page, offset = qc.scroll("aike_chunks", limit=1000, offset=offset, with_payload=True)
        records.extend(page)
        if offset is None:
            break
            
    docs_chunks = defaultdict(list)
    for r in records:
        if not r.payload:
            continue
        doc = r.payload.get("doc")
        code = r.payload.get("code")
        lang = r.payload.get("lang")
        
        if not doc or not code:
            continue
            
        code = str(code).strip()
        if len(code) < 4:
            continue
            
        # Raccogliamo per lang per facilitare l'estrazione
        docs_chunks[(doc, lang)].append(r)
        
    random.seed(42) # fixed seed for reproducibility
    
    doc_roles = get_document_roles()
    
    # Separiamo in buckets per lingua
    de_buckets = [(d, l) for (d, l) in docs_chunks.keys() if l == "DE"]
    other_buckets = [(d, l) for (d, l) in docs_chunks.keys() if l != "DE"]
    
    random.shuffle(de_buckets)
    random.shuffle(other_buckets)
    
    sampled_codes = []
    doc_code_counts = defaultdict(int)
    
    # Obbligo: almeno 4 codici DE
    for (doc, lang) in de_buckets:
        if len(sampled_codes) >= 4:
            break
        role = doc_roles.get(doc)
        if not role:
            continue
        chunks = docs_chunks[(doc, lang)]
        random.shuffle(chunks)
        for r in chunks:
            c = str(r.payload["code"]).strip()
            if doc_code_counts[doc] < 5:
                sampled_codes.append({
                    "code": c,
                    "doc": doc,
                    "lang": lang,
                    "role": role
                })
                doc_code_counts[doc] += 1
                break # 1 per doc in this pass to distribute
                
    # Aggiungiamo dal resto per arrivare a 40
    all_buckets = de_buckets + other_buckets
    random.shuffle(all_buckets)
    
    for (doc, lang) in all_buckets:
        if len(sampled_codes) >= 40:
            break
        role = doc_roles.get(doc)
        if not role:
            continue
        chunks = docs_chunks[(doc, lang)]
        random.shuffle(chunks)
        
        for r in chunks:
            if len(sampled_codes) >= 40:
                break
            c = str(r.payload["code"]).strip()
            # check duplicate code in sample
            if any(s["code"] == c and s["doc"] == doc for s in sampled_codes):
                continue
            if doc_code_counts[doc] < 5:
                sampled_codes.append({
                    "code": c,
                    "doc": doc,
                    "lang": lang,
                    "role": role
                })
                doc_code_counts[doc] += 1

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
        
        with open(out_dir / "b1b_degraded_codes_v2.json", "w", encoding="utf-8") as f:
            json.dump(output_data, f, indent=2, ensure_ascii=False)
            
    print(f"Audit completed in {datetime.datetime.now() - start_time}. Data saved.")

if __name__ == "__main__":
    main()
