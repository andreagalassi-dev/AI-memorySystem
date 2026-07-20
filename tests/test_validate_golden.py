import pytest
from unittest.mock import MagicMock
from scripts.validate_golden import GoldenValidator

class MockQdrant:
    def __init__(self):
        self.responses = []
    def scroll(self, collection_name, scroll_filter, limit):
        if self.responses:
            return [self.responses.pop(0)]
        return [[]]

class MockPG:
    def __init__(self):
        self.cursor_mock = MagicMock()
        self.cursor_mock.fetchone.return_value = None
    def cursor(self):
        class Ctx:
            def __init__(self, mock):
                self.mock = mock
            def __enter__(self):
                return self.mock
            def __exit__(self, *args):
                pass
        return Ctx(self.cursor_mock)

def test_validator_code_qdrant_hit():
    q = MockQdrant()
    q.responses = [[MagicMock()]] # Hit
    p = MockPG()
    v = GoldenValidator(q, p)
    
    row = {"intent": "code", "expected": {"doc": "D", "page": 1, "code": "C"}}
    ok, err = v.validate_row(row)
    assert ok, err

def test_validator_code_pg_hit():
    q = MockQdrant()
    q.responses = [[]] # Miss Qdrant
    p = MockPG()
    p.cursor_mock.fetchone.return_value = (1,) # Hit PG
    v = GoldenValidator(q, p)
    
    row = {"intent": "code", "expected": {"doc": "D", "page": 1, "code": "C"}}
    ok, err = v.validate_row(row)
    assert ok, err

def test_validator_code_miss():
    q = MockQdrant()
    q.responses = [[]]
    p = MockPG()
    p.cursor_mock.fetchone.return_value = None
    v = GoldenValidator(q, p)
    
    row = {"intent": "code", "expected": {"doc": "D", "page": 1, "code": "C"}}
    ok, err = v.validate_row(row)
    assert not ok

def test_rbac_negative_fails_if_accessible():
    q = MockQdrant()
    p = MockPG()
    p.cursor_mock.fetchone.return_value = (1,) # Role has access!
    v = GoldenValidator(q, p)
    
    row = {"intent": "rbac_negative", "role": "commerciale", "expected": {"doc": "test_doc"}}
    ok, err = v.validate_row(row)
    assert not ok
    assert "accessibile al ruolo" in err or "accessible to role" in err
