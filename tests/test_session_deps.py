import pytest
import sys
import importlib
from src.serving.session_deps import get_deps
from src.graph.pipeline import parse_role

def test_get_deps_cache():
    deps1 = get_deps("tecnico")
    deps2 = get_deps("tecnico")
    assert deps1 is deps2

def test_get_deps_shared_singletons():
    deps1 = get_deps("tecnico")
    deps2 = get_deps("commerciale")
    
    assert deps1 is not deps2
    assert deps1.llm is deps2.llm
    if deps1.visual_search is not None:
        assert deps1.visual_search is deps2.visual_search

def test_lazy_import(monkeypatch):
    import sys
    # Rimuoviamo moduli pesanti in modo sicuro con monkeypatch
    for mod in ["torch", "mlx", "mlx.core"]:
        monkeypatch.delitem(sys.modules, mod, raising=False)
    
    import src.serving.session_deps
    importlib.reload(src.serving.session_deps)
    
    assert "torch" not in sys.modules
    assert "mlx.core" not in sys.modules

def test_degradation(monkeypatch, caplog):
    import psycopg
    
    def mock_connect(*args, **kwargs):
        raise psycopg.OperationalError("Mocked DB down")
        
    monkeypatch.setattr(psycopg, "connect", mock_connect)
    
    # We need to clear cache to force new initialization if we test degradation properly
    import src.serving.session_deps
    src.serving.session_deps._CACHE = {}
    
    deps = get_deps("tecnico")
    assert deps.sql_query is None
    assert "Ramo STRUCTURED degradato" in caplog.text

def test_get_serving_health():
    import src.serving.session_deps as sd
    
    # Reset for test
    sd._CACHE.clear()
    sd._llm = None
    sd._visual_search = None
    sd._multiplexer = None
    
    health = sd.get_serving_health()
    assert health["resident_model"] is None
    assert health["roles_cached"] == []
    
    # Initialize deps
    get_deps("tecnico")
    
    health = sd.get_serving_health()
    assert health["resident_model"] is None
    assert "tecnico" in health["roles_cached"]
    
    # Simulate a load
    sd._multiplexer.resident = lambda: {"test_model"}
    health = sd.get_serving_health()
    assert health["resident_model"] == "test_model"
