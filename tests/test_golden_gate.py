import pytest
from scripts.golden_gate import (
    evaluate_code,
    evaluate_visual,
    evaluate_narrative,
    evaluate_refusal,
    evaluate_rbac_negative,
    evaluate_stability,
    compare_to_baseline,
    has_citation
)

# Mock classes for citations
class MockHit:
    def __init__(self, doc, page):
        self.doc = doc
        self.page = page

def test_has_citation():
    citations = [MockHit("doc1", 10), MockHit("doc2", 20)]
    assert has_citation(citations, "doc1", 10) is True
    assert has_citation(citations, "doc1", 11) is False
    assert has_citation(citations, "doc3", 20) is False

def test_has_citation_real_format():
    citations_real = [["Mamoli_Catalogo_Ricambi_2021_IT_EN", 100]]
    assert has_citation(citations_real, "Mamoli_Catalogo_Ricambi_2021_IT_EN", 100) is True
    assert has_citation(citations_real, "Mamoli_Catalogo_Ricambi_2021_IT_EN", 101) is False
    assert has_citation(citations_real, "Other", 100) is False

def test_evaluate_code_real_format():
    citations_real = [["Mamoli_Catalogo_Ricambi_2021_IT_EN", 100]]
    res = evaluate_code("ALTA", citations_real, "Mamoli_Catalogo_Ricambi_2021_IT_EN", 100)
    assert res["status"] == "PASS_ALTA"
    assert res["hit"] is True

def test_evaluate_code_index_aware():
    citations = [["wrong_doc", 999]]
    
    def mock_check_index(doc, page, code):
        if doc == "wrong_doc" and page == 999 and code == "MY_CODE":
            return True
        return False
        
    res = evaluate_code("ALTA", citations, "expected_doc", 100, "MY_CODE", check_index_fn=mock_check_index)
    assert res["status"] == "PASS_ALTA"
    assert res["hit"] is True
    assert res.get("matched_expected_page") is False

def test_evaluate_code():
    citations = [MockHit("doc1", 10)]
    
    # PASS_ALTA
    res = evaluate_code("ALTA", citations, "doc1", 10)
    assert res["status"] == "PASS_ALTA"
    assert res["hit"] is True
    
    # WRONG_ALTA
    res = evaluate_code("ALTA", citations, "doc1", 20)
    assert res["status"] == "WRONG_ALTA"
    assert res["hit"] is False
    
    # MISS (non alta)
    res = evaluate_code("MEDIA", citations, "doc1", 20)
    assert res["status"] == "MISS"
    assert res["hit"] is False
    
    # Hit ma livello basso (non è PASS_ALTA, ma registra l'hit)
    res = evaluate_code("MEDIA", citations, "doc1", 10)
    assert res["status"] == "MISS"
    assert res["hit"] is True

def test_evaluate_visual():
    visual_pages = [MockHit("doc1", 10), MockHit("doc1", 11)]
    
    assert evaluate_visual(visual_pages, "doc1", [10, 15])["hit"] is True
    assert evaluate_visual(visual_pages, "doc1", [15])["hit"] is False
    assert evaluate_visual(visual_pages, "doc2", [10])["hit"] is False

def test_evaluate_narrative():
    citations = [MockHit("doc1", 10)]
    res = evaluate_narrative("MEDIA", citations, "doc1", 10)
    assert res["hit"] is True
    assert res["level"] == "MEDIA"
    
    res2 = evaluate_narrative("ALTA", citations, "doc1", 15)
    assert res2["hit"] is False

def test_evaluate_refusal():
    assert evaluate_refusal("REFUSAL", [])["hit"] is True
    assert evaluate_refusal("BASSA", [])["hit"] is True
    assert evaluate_refusal("ALTA", [])["hit"] is False
    assert evaluate_refusal("BASSA", [MockHit("doc", 1)])["hit"] is False

def test_evaluate_rbac_negative():
    assert evaluate_rbac_negative("REFUSAL", [], "restricted_doc")["status"] == "PASS"
    assert evaluate_rbac_negative("ALTA", [MockHit("other", 1)], "restricted_doc")["status"] == "PASS"
    
    res = evaluate_rbac_negative("MEDIA", [MockHit("restricted_doc", 10)], "restricted_doc")
    assert res["status"] == "RBAC_LEAK"

def test_evaluate_stability():
    runs_stable = [
        {"final_level": "ALTA", "hit": True},
        {"final_level": "ALTA", "hit": True},
        {"final_level": "ALTA", "hit": True}
    ]
    assert evaluate_stability(runs_stable) == "STABILE"
    
    runs_unstable_level = [
        {"final_level": "ALTA", "hit": True},
        {"final_level": "MEDIA", "hit": True},
        {"final_level": "ALTA", "hit": True}
    ]
    assert evaluate_stability(runs_unstable_level) == "UNSTABLE"
    
    runs_unstable_hit = [
        {"final_level": "ALTA", "hit": True},
        {"final_level": "ALTA", "hit": False},
        {"final_level": "ALTA", "hit": True}
    ]
    assert evaluate_stability(runs_unstable_hit) == "UNSTABLE"

def test_compare_to_baseline():
    base = {"hit": True, "final_level": "ALTA", "stable": "STABILE", "citation_ok": True}
    
    # No regression
    curr = {"hit": True, "final_level": "ALTA", "stable": "STABILE", "citation_ok": True}
    assert compare_to_baseline(base, curr)[0] is False
    
    # Hit regression
    curr2 = {"hit": False, "final_level": "ALTA", "stable": "STABILE", "citation_ok": True}
    assert compare_to_baseline(base, curr2)[0] is True
    
    # Level regression
    curr3 = {"hit": True, "final_level": "MEDIA", "stable": "STABILE", "citation_ok": True}
    assert compare_to_baseline(base, curr3)[0] is True
    
    # Level regression (MEDIA to BASSA)
    base_media = {"hit": True, "final_level": "MEDIA", "stable": "STABILE", "citation_ok": True}
    curr_bassa = {"hit": True, "final_level": "BASSA", "stable": "STABILE", "citation_ok": True}
    assert compare_to_baseline(base_media, curr_bassa)[0] is True
    
    # Stability regression
    curr4 = {"hit": True, "final_level": "ALTA", "stable": "UNSTABLE", "citation_ok": True}
    assert compare_to_baseline(base, curr4)[0] is True
    
    # Citation ok regression
    curr5 = {"hit": True, "final_level": "ALTA", "stable": "STABILE", "citation_ok": False}
    assert compare_to_baseline(base, curr5)[0] is True

    # No regression if baseline had False/BASSA and current is better/same
    base_low = {"hit": False, "final_level": "BASSA", "stable": "UNSTABLE", "citation_ok": False}
    curr_high = {"hit": True, "final_level": "ALTA", "stable": "STABILE", "citation_ok": True}
    assert compare_to_baseline(base_low, curr_high)[0] is False


def test_audit_payload_porta_la_config_di_primo_livello():
    """Un audit senza la configurazione che l'ha prodotto non e' interpretabile."""
    import json

    from scripts.golden_gate import build_audit_payload

    cfg = {"visual_rerank": {"enabled": True, "reason": "env AIKE_VISUAL_RERANK='1' (esplicito)"},
           "visual_score_floor": 0.20}
    results = {"V001": {"status": "PASS"}, "V002": {"status": "MISS"}}

    payload = build_audit_payload(cfg, results, "data/golden/golden_v1.jsonl", 1234.5)

    assert payload["config"] is cfg
    assert payload["results"] == results
    assert payload["run"]["n_queries"] == 2
    assert payload["run"]["dataset"] == "data/golden/golden_v1.jsonl"
    assert payload["run"]["elapsed_s"] == 1234.5
    assert payload["run"]["generated_at"]
    json.dumps(payload)  # deve restare serializzabile
