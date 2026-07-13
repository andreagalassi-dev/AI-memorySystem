import pytest
import numpy as np
from unittest.mock import MagicMock
from scripts.index_colqwen3 import check_roundtrip, validation_gate

def test_check_roundtrip_success():
    client = MagicMock()
    # Mocking Qdrant response
    mock_point = MagicMock()
    mock_point.vector = {"": [[0.1, 0.2], [0.3, 0.4]]}
    client.retrieve.return_value = [mock_point]
    
    orig_vector = [[0.105, 0.201], [0.3, 0.4]]
    # Mean diff = (0.005 + 0.001 + 0 + 0) / 4 = 0.0015 < 0.01
    
    assert check_roundtrip(client, "dummy-id", orig_vector, tolerance=0.01) is True


def test_check_roundtrip_failure_diff():
    client = MagicMock()
    mock_point = MagicMock()
    mock_point.vector = {"": [[0.1, 0.2], [0.3, 0.4]]}
    client.retrieve.return_value = [mock_point]
    
    orig_vector = [[0.2, 0.2], [0.3, 0.4]]
    # Mean diff = (0.1 + 0 + 0 + 0) / 4 = 0.025 >= 0.01
    
    assert check_roundtrip(client, "dummy-id", orig_vector, tolerance=0.01) is False


def test_check_roundtrip_failure_shape():
    client = MagicMock()
    mock_point = MagicMock()
    mock_point.vector = {"": [[0.1, 0.2], [0.3, 0.4]]}
    client.retrieve.return_value = [mock_point]
    
    orig_vector = [[0.1, 0.2]] # Different shape
    
    assert check_roundtrip(client, "dummy-id", orig_vector, tolerance=0.01) is False


def test_validation_gate_success(monkeypatch):
    client = MagicMock()
    
    # Mock Image and embed_pages to avoid actual model loading
    monkeypatch.setattr("scripts.index_colqwen3.Image.open", MagicMock())
    monkeypatch.setattr("scripts.index_colqwen3.embed_pages", lambda x: [[[0.1]] for _ in x])
    
    done_meta = [{"doc": "test_doc", "page": 1, "render": "dummy.png"}]
    
    # Mocking query_points to return the correct point with score >= 5x
    mock_hit1 = MagicMock()
    from scripts.index_colqwen3 import page_id
    mock_hit1.id = page_id("test_doc", 1)
    mock_hit1.score = 0.9
    
    mock_hit2 = MagicMock()
    mock_hit2.id = "other-id"
    mock_hit2.score = 0.15
    # 0.9 >= 5 * 0.15 (0.9 >= 0.75) -> Success!
    
    mock_result = MagicMock()
    mock_result.points = [mock_hit1, mock_hit2]
    client.query_points.return_value = mock_result
    
    assert validation_gate(client, done_meta) is True


def test_validation_gate_failure_score(monkeypatch):
    client = MagicMock()
    
    monkeypatch.setattr("scripts.index_colqwen3.Image.open", MagicMock())
    monkeypatch.setattr("scripts.index_colqwen3.embed_pages", lambda x: [[[0.1]] for _ in x])
    
    done_meta = [{"doc": "test_doc", "page": 1, "render": "dummy.png"}]
    
    mock_hit1 = MagicMock()
    from scripts.index_colqwen3 import page_id
    mock_hit1.id = page_id("test_doc", 1)
    mock_hit1.score = 0.9
    
    mock_hit2 = MagicMock()
    mock_hit2.id = "other-id"
    mock_hit2.score = 0.2
    # 0.9 >= 5 * 0.2 (0.9 >= 1.0) -> False -> Failure
    
    mock_result = MagicMock()
    mock_result.points = [mock_hit1, mock_hit2]
    client.query_points.return_value = mock_result
    
    assert validation_gate(client, done_meta) is False


def test_validation_gate_failure_not_top1(monkeypatch):
    client = MagicMock()
    
    monkeypatch.setattr("scripts.index_colqwen3.Image.open", MagicMock())
    monkeypatch.setattr("scripts.index_colqwen3.embed_pages", lambda x: [[[0.1]] for _ in x])
    
    done_meta = [{"doc": "test_doc", "page": 1, "render": "dummy.png"}]
    
    mock_hit1 = MagicMock()
    mock_hit1.id = "wrong-id"
    mock_hit1.score = 0.9
    
    mock_result = MagicMock()
    mock_result.points = [mock_hit1]
    client.query_points.return_value = mock_result
    
    assert validation_gate(client, done_meta) is False
