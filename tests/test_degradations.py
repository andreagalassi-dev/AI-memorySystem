import pytest
import logging
from src.graph.pipeline import _default_deps
from src.common.roles import Role

def test_default_deps_structured_fallback_logs_warning(caplog, monkeypatch):
    def mock_get_conn(*args, **kwargs):
        raise RuntimeError("DB in fiamme")
    
    monkeypatch.setattr("psycopg.connect", mock_get_conn)
    
    with caplog.at_level(logging.WARNING):
        deps = _default_deps(Role.TECNICO)
        
    assert deps.sql_query is None
    
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("structured" in msg.lower() and "RuntimeError" in msg for msg in warnings), f"Warning not found in: {warnings}"


def test_default_deps_visual_fallback_logs_warning(caplog, monkeypatch):
    def mock_make_visual(*args, **kwargs):
        raise RuntimeError("Qdrant esploso")
        
    monkeypatch.setattr("src.graph.tools.visual_tool.make_visual_search", mock_make_visual)
    
    with caplog.at_level(logging.WARNING):
        deps = _default_deps(Role.TECNICO)
        
    assert deps.visual_search is None
    
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("visual" in msg.lower() and "RuntimeError" in msg for msg in warnings), f"Warning not found in: {warnings}"
