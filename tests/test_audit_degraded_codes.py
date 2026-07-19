import pytest
from scripts.audit_degraded_codes import classify_root_cause

def test_classify_alta():
    res = classify_root_cause("12345", "ALTA", [{"code": "12345"}], "PASS", "Trovato il codice 12345.")
    assert res == "ALTA"

def test_classify_retrieval_miss():
    # Not in candidates
    res = classify_root_cause("12345", "BASSA", [{"code": "54321"}], "PASS", "Non lo so")
    assert res == "RETRIEVAL_MISS"

def test_classify_generazione():
    # In candidates, but guardrail blocks/verifies because of bad generation
    res = classify_root_cause("12345", "MEDIA", [{"code": "12345"}], "VERIFY", "Il codice è 12345 ma...")
    assert res == "GENERAZIONE"

    res = classify_root_cause("12345", "BASSA", [{"code": "12345"}], "BLOCK", "Codice inventato 999")
    assert res == "GENERAZIONE"

def test_classify_fusione_ranking():
    # In candidates, guardrail PASS, but level is not ALTA (e.g. low confidence -> MEDIA or BASSA)
    res = classify_root_cause("12345", "MEDIA", [{"code": "12345"}], "PASS", "Il codice 12345 esiste.")
    assert res == "FUSIONE_RANKING"
