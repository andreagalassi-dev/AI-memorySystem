import pytest
from src.graph.tools.sql_tool import LookupRow
from src.retrieval.price_variants import aggregate_price_variants, PriceVariant

def test_aggregate_price_variants_mono_price():
    rows = [
        LookupRow(valore=19.50, doc_stem="doc1", page=10, version_status="current", ingested_at="2023-01-01"),
        LookupRow(valore=19.50, doc_stem="doc1", page=11, version_status="current", ingested_at="2023-01-01")
    ]
    ans = aggregate_price_variants(rows)
    assert ans.principale.prezzo == 19.50
    assert ans.principale.pagine == [10, 11]
    assert ans.principale.superseded is False
    assert ans.principale.freq == 2
    assert len(ans.varianti) == 0

def test_aggregate_price_variants_mamoli_like():
    rows = [
        # principale 19.50 (freq 3)
        LookupRow(valore=19.50, doc_stem="doc1", page=1, version_status="current", ingested_at="2023"),
        LookupRow(valore=19.50, doc_stem="doc1", page=2, version_status="current", ingested_at="2023"),
        LookupRow(valore=19.50, doc_stem="doc1", page=3, version_status="current", ingested_at="2023"),
        # variante 18.20 (freq 1, pag 34)
        LookupRow(valore=18.20, doc_stem="doc1", page=34, version_status="current", ingested_at="2023"),
        # variante 21.50 (freq 1, pag 188)
        LookupRow(valore=21.50, doc_stem="doc1", page=188, version_status="current", ingested_at="2023")
    ]
    ans = aggregate_price_variants(rows)
    
    assert ans.principale.prezzo == 19.50
    assert ans.principale.freq == 3
    
    assert len(ans.varianti) == 2
    varianti_prezzi = [v.prezzo for v in ans.varianti]
    assert 18.20 in varianti_prezzi
    assert 21.50 in varianti_prezzi
    
    for v in ans.varianti:
        if v.prezzo == 18.20:
            assert v.pagine == [34]
        if v.prezzo == 21.50:
            assert v.pagine == [188]

def test_aggregate_price_variants_superseded_priority():
    rows = [
        # superseded price with higher freq (3)
        LookupRow(valore=10.00, doc_stem="doc1", page=1, version_status="superseded", ingested_at="2023"),
        LookupRow(valore=10.00, doc_stem="doc1", page=2, version_status="superseded", ingested_at="2023"),
        LookupRow(valore=10.00, doc_stem="doc1", page=3, version_status="superseded", ingested_at="2023"),
        # current price with lower freq (1) -> SHOULD WIN
        LookupRow(valore=20.00, doc_stem="doc2", page=5, version_status="current", ingested_at="2023")
    ]
    ans = aggregate_price_variants(rows)
    assert ans.principale.prezzo == 20.00
    assert ans.principale.superseded is False
    assert len(ans.varianti) == 1
    assert ans.varianti[0].prezzo == 10.00
    assert ans.varianti[0].superseded is True

def test_aggregate_price_variants_tie_break_pages_and_ingested():
    rows = [
        # val=10: freq 2, pages 2, ingested 'A'
        LookupRow(valore=10.0, doc_stem="doc1", page=1, version_status=None, ingested_at="A"),
        LookupRow(valore=10.0, doc_stem="doc1", page=2, version_status=None, ingested_at="A"),
        
        # val=20: freq 2, pages 1, ingested 'Z' (loses because pages=1 vs 2)
        LookupRow(valore=20.0, doc_stem="doc2", page=5, version_status=None, ingested_at="Z"),
        LookupRow(valore=20.0, doc_stem="doc2", page=5, version_status=None, ingested_at="Z"),
        
        # val=30: freq 2, pages 2, ingested 'B' (wins over val=10 because 'B' > 'A')
        LookupRow(valore=30.0, doc_stem="doc3", page=7, version_status=None, ingested_at="B"),
        LookupRow(valore=30.0, doc_stem="doc3", page=8, version_status=None, ingested_at="B"),
    ]
    ans = aggregate_price_variants(rows)
    
    assert ans.principale.prezzo == 30.0
    assert ans.varianti[0].prezzo == 10.0
    assert ans.varianti[1].prezzo == 20.0

def test_aggregate_price_variants_null_status():
    rows = [
        # NULL version_status means NOT superseded
        LookupRow(valore=100.0, doc_stem="doc", page=1, version_status=None, ingested_at=None)
    ]
    ans = aggregate_price_variants(rows)
    assert ans.principale.superseded is False
