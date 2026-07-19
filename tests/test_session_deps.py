import pytest
import sys
import importlib
import threading
import time
from src.serving.session_deps import get_deps
from src.graph.pipeline import parse_role, Deps

def test_get_deps_cache():
    deps1 = get_deps("tecnico")
    deps2 = get_deps("tecnico")
    assert deps1 is deps2

def test_get_deps_shared_singletons():
    deps1 = get_deps("tecnico")
    deps2 = get_deps("commerciale")
    
    assert deps1 is not deps2
    if hasattr(deps1.llm, 'target'):
        assert deps1.llm.target is deps2.llm.target
    else:
        assert deps1.llm is deps2.llm
    if deps1.visual_search is not None:
        if hasattr(deps1.visual_search, '__wrapped__'):
            assert deps1.visual_search.__wrapped__ is deps2.visual_search.__wrapped__
        else:
            # We can't easily check wrapper targets in Python without more introspection, but checking closures works
            assert deps1.visual_search.__closure__[0].cell_contents is deps2.visual_search.__closure__[0].cell_contents

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

def test_init_shared_singletons_concurrent_is_thread_safe(monkeypatch):
    import src.serving.session_deps as sd
    
    sd._CACHE.clear()
    sd._llm = None
    sd._visual_search = None
    sd._multiplexer = None
    
    calls = []
    
    def mock_build_default_deps(*args, **kwargs):
        from src.retrieval.confidence import Thresholds
        return Deps(retrieve=lambda: None, synthesize=lambda: None, catalog_lookup=lambda: None, thresholds=Thresholds(0,0))
    monkeypatch.setattr("src.serving.session_deps.build_default_deps", mock_build_default_deps)

    def mock_MLXModel(*args, **kwargs):
        calls.append("MLXModel_init")
        time.sleep(0.1) # Simulate slow init
        return "mock_primary"
    monkeypatch.setattr("src.graph.llm.MLXModel", mock_MLXModel)
    
    threads = []
    for _ in range(10):
        t = threading.Thread(target=sd.get_deps, args=("tecnico",))
        threads.append(t)
        
    for t in threads:
        t.start()
        
    for t in threads:
        t.join()
        
    assert len(calls) == 1

def test_inference_is_serialized(monkeypatch):
    import src.serving.session_deps as sd
    sd._CACHE.clear()
    sd._llm = None
    
    class FakeSlowLLM:
        def __init__(self):
            self.calls = []
        def generate(self, system, user):
            start = time.time()
            time.sleep(0.2)
            end = time.time()
            self.calls.append((start, end))
            return "ok"
            
    fake_llm = FakeSlowLLM()
    monkeypatch.setattr(sd, "_llm", fake_llm)
    
    def mock_build_default(*args, llm=None, visual_search=None):
        from src.retrieval.confidence import Thresholds
        return Deps(
            retrieve=lambda: None,
            synthesize=lambda: None,
            catalog_lookup=lambda: None,
            thresholds=Thresholds(0,0),
            llm=fake_llm
        )
    monkeypatch.setattr("src.serving.session_deps.build_default_deps", mock_build_default)
    
    deps = sd.get_deps("tecnico")
    
    threads = []
    def worker():
        deps.llm.generate("sys", "user")
        
    for _ in range(2):
        t = threading.Thread(target=worker)
        threads.append(t)
        
    for t in threads:
        t.start()
    for t in threads:
        t.join()
        
    assert len(fake_llm.calls) == 2
    (start1, end1) = fake_llm.calls[0]
    (start2, end2) = fake_llm.calls[1]
    
    # Check serialization: one ends before the next begins
    assert end1 <= start2 or end2 <= start1
