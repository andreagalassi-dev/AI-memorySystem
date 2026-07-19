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


def test_validation_gate_success():
    from scripts.run_gate_colqwen3 import check_sample
    
    mock_hit1 = MagicMock()
    mock_hit1.id = "test-id"
    mock_hit1.score = 9.0
    
    mock_hit2 = MagicMock()
    mock_hit2.id = "other-id"
    mock_hit2.score = 8.9
    
    # 9.0 / 10 = 0.90 >= 0.85
    ok, reason = check_sample("test-id", [mock_hit1, mock_hit2], 10)
    assert ok is True
    assert reason is None


def test_validation_gate_failure_score_per_token():
    from scripts.run_gate_colqwen3 import check_sample
    
    mock_hit1 = MagicMock()
    mock_hit1.id = "test-id"
    mock_hit1.score = 8.0
    
    # 8.0 / 10 = 0.80 < 0.85
    ok, reason = check_sample("test-id", [mock_hit1], 10)
    assert ok is False
    assert reason == "score_per_token"


def test_validation_gate_failure_not_top1():
    from scripts.run_gate_colqwen3 import check_sample
    
    mock_hit1 = MagicMock()
    mock_hit1.id = "wrong-id"
    mock_hit1.score = 9.0
    
    ok, reason = check_sample("test-id", [mock_hit1], 10)
    assert ok is False
    assert reason == "non-top1"


def test_validation_gate_orchestration_success(monkeypatch):
    import subprocess
    import os
    
    called_with_file = None
    
    def mock_run(cmd, check):
        nonlocal called_with_file
        called_with_file = cmd[2]
        assert os.path.exists(called_with_file)
        return MagicMock(returncode=0)
        
    monkeypatch.setattr(subprocess, "run", mock_run)
    
    done_meta = [{"doc": "test_doc", "page": 1, "render": "dummy.png"}]
    res = validation_gate(None, done_meta)
    
    assert res is True
    assert called_with_file is not None
    assert not os.path.exists(called_with_file)


def test_validation_gate_orchestration_failure(monkeypatch):
    import subprocess
    monkeypatch.setattr(subprocess, "run", lambda cmd, check: MagicMock(returncode=1))
    
    done_meta = [{"doc": "test_doc", "page": 1, "render": "dummy.png"}]
    res = validation_gate(None, done_meta)
    
    assert res is False

