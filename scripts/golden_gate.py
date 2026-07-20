import sys
import os
import json
from datetime import datetime

def has_citation(citations, expected_doc, expected_page=None):
    for c in citations:
        doc = getattr(c, "doc", c.get("doc") if isinstance(c, dict) else None)
        page = getattr(c, "page", c.get("page") if isinstance(c, dict) else None)
        if doc == expected_doc:
            if expected_page is None or page == expected_page:
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

def evaluate_code(final_level, citations, expected_doc, expected_page):
    final_level = (final_level or "").upper()
    hit_citation = has_citation(citations, expected_doc, expected_page)
    
    if hit_citation and final_level == "ALTA":
        status = "PASS_ALTA"
    elif not hit_citation and final_level == "ALTA":
        status = "WRONG_ALTA"
    else:
        status = "MISS"
        
    return {
        "hit": hit_citation,
        "status": status,
        "level": final_level
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
