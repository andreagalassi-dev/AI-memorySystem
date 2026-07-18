from fastapi.testclient import TestClient
import pytest
from src.backend.main import app
from tests.conftest import needs_pg, needs_qdrant

client = TestClient(app)

@needs_qdrant
@needs_pg
def test_health_ok():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["backend"] == "ok"
    assert data["qdrant"] == "ok"
    assert data["structured"] == "ok"
    assert data["visual"] == "ok"
    assert data["narrative"] == "ok"
    assert "logic_model" in data
    assert data["resident_model"] is None

@needs_qdrant
def test_health_structured_degraded(monkeypatch):
    def mock_get_conn():
        raise RuntimeError("DB irraggiungibile")
        
    monkeypatch.setattr("src.backend.clients.postgres_client.get_pg_conn_readonly", mock_get_conn)
    
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["backend"] == "ok"
    assert data["qdrant"] == "ok"
    assert "structured: degraded" in f"structured: {data['structured']}"
    assert "RuntimeError" in data["structured"]
    assert data["visual"] == "ok"
