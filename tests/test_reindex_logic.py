from src.retrieval.pixelrag.ingest import decide_recreate, get_successful_pages

def test_decide_recreate():
    assert decide_recreate(False, None) is True
    assert decide_recreate(False, "0") is True
    assert decide_recreate(True, None) is False
    assert decide_recreate(True, "0") is False
    assert decide_recreate(True, "1") is True

def test_successful_pages():
    batch = [("doc1", 1), ("doc1", 2), ("doc2", 1)]
    failed = set([("doc1", 2)])
    assert get_successful_pages(batch, failed) == [("doc1", 1), ("doc2", 1)]
