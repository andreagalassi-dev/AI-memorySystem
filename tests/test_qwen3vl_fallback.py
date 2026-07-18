from unittest.mock import MagicMock
from qdrant_client import models
from src.retrieval.qwen3vl import doc_hint_filter
from src.retrieval.pixelrag.index import QdrantVisualIndex

def test_v009_doc_hint_filter(monkeypatch):
    # Fake cache to contain KBS documenti
    monkeypatch.setattr("src.retrieval.qwen3vl._indexed_docs_cache", lambda: ("kbs_gastrotechnik_1717", "other_doc"))
    
    query = "esploso del forno KBS gastronomia"
    flt = doc_hint_filter(query)
    
    assert flt is not None
    # 'kbs' has 3 letters, so it should be matched
    matched_docs = flt.must[0].match.any
    assert "kbs_gastrotechnik_1717" in matched_docs
    assert "other_doc" not in matched_docs


def test_search_visual_fallback(monkeypatch):
    query = "esploso del forno KBS gastronomia"
    
    # Mock doc hint filter to return a dummy filter
    dummy_filter = models.Filter(must=[models.FieldCondition(key="doc", match=models.MatchAny(any=["dummy"]))])
    monkeypatch.setattr("src.retrieval.qwen3vl.doc_hint_filter", lambda q: dummy_filter)

    client = MagicMock()
    index = QdrantVisualIndex(client)
    
    # 1. First call returns < 10 distinct pages
    class DummyPoint:
        def __init__(self, doc, page):
            self.id = 1
            self.score = 0.9
            self.payload = {"doc": doc, "page": page}
            
    # Return 2 distinct pages (less than 10)
    first_response = MagicMock()
    first_response.points = [DummyPoint("kbs_gastrotechnik_1717", 1), DummyPoint("kbs_gastrotechnik_1717", 2)]
    
    # Second call returns 15 distinct pages
    second_response = MagicMock()
    second_response.points = [DummyPoint("other", i) for i in range(15)]
    
    client.query_points.side_effect = [first_response, second_response]
    
    hits = index.search([0.1, 0.2], k=30, query_text=query)
    
    # It should have retried without the filter
    assert client.query_points.call_count == 2
    
    # Check that second call dropped the hint filter (so it's None or only RBAC base_filter)
    args, kwargs = client.query_points.call_args_list[1]
    assert kwargs.get("query_filter") is None
    assert len(hits) == 15
