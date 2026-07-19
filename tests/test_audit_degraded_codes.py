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

from unittest.mock import patch, MagicMock

def test_build_sample_v2_paginated():
    # Mock qdrant records
    # We need at least 4 DE, some IT, EN, and docs > 8
    # We create a mock generator/scroll
    mock_records = []
    
    # 4 DE codes
    for i in range(5):
        mock_records.append(MagicMock(payload={"code": f"DE_CODE_{i}", "lang": "DE", "doc": f"doc_de_{i}"}))
    # 20 IT codes
    for i in range(20):
        mock_records.append(MagicMock(payload={"code": f"IT_CODE_{i}", "lang": "IT", "doc": f"doc_it_{i//5}"}))
    # 20 EN codes
    for i in range(20):
        mock_records.append(MagicMock(payload={"code": f"EN_CODE_{i}", "lang": "EN", "doc": f"doc_en_{i//5}"}))
    # Short code
    mock_records.append(MagicMock(payload={"code": "123", "lang": "IT", "doc": "doc_it_0"}))
    
    def mock_scroll(collection, limit=10000, offset=None, with_payload=True):
        page_size = 10
        start = offset or 0
        end = start + page_size
        page = mock_records[start:end]
        next_offset = end if end < len(mock_records) else None
        return page, next_offset
        
    def mock_get_document_roles():
        return {r.payload["doc"]: "tecnico" for r in mock_records}

    with patch('scripts.audit_degraded_codes.get_qdrant_client') as mock_qc, \
         patch('scripts.audit_degraded_codes.get_document_roles', side_effect=mock_get_document_roles):
        client = MagicMock()
        client.scroll.side_effect = mock_scroll
        mock_qc.return_value = client
        
        from scripts.audit_degraded_codes import build_sample
        sample = build_sample()
        
        # Verify 40 samples
        assert len(sample) == 40
        # Verify pagination was used
        assert client.scroll.call_count > 1
        # Verify min DE = 4
        de_codes = [s for s in sample if s["lang"] == "DE"]
        assert len(de_codes) >= 4
        # Verify no short codes
        for s in sample:
            assert len(s["code"]) >= 4
        # Verify at least 8 docs
        docs = set(s["doc"] for s in sample)
        assert len(docs) >= 8

