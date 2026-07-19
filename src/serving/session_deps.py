"""Dependencies for the serving session, providing shared singletons."""
import logging
from typing import Optional

from src.graph.pipeline import Deps, build_default_deps, parse_role

logger = logging.getLogger(__name__)

# Singletons (lazy initialization)
_llm = None
_visual_search = None
_multiplexer = None
_CACHE: dict[str, Deps] = {}

def _init_shared_singletons():
    global _llm, _visual_search
    if _llm is not None:
        return
        
    # Initialize shared MLXModel + Multiplexer
    from src.graph.llm import MLXModel, FallbackLLM
    from src.serving.multiplexer import ModelMultiplexer, MlxModelRuntime
    from src.backend.clients.ollama_client import OllamaModel
    from src.backend.config import settings

    global _multiplexer
    mlx_runtime = MlxModelRuntime(default_num_ctx=settings.local_ctx_cap)
    _multiplexer = ModelMultiplexer(mlx_runtime, max_resident=1, ctx_cap=settings.local_ctx_cap)
    
    primary_llm = MLXModel(settings.logic_model, _multiplexer)
    fallback_llm = OllamaModel()
    
    _llm = FallbackLLM(primary_llm, fallback_llm)
    
    # Initialize shared Visual Search
    try:
        from src.backend.clients.qdrant_client import get_qdrant_client
        from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder
        from src.retrieval.pixelrag.index import QdrantVisualIndex
        from src.graph.tools.visual_tool import make_visual_search
        
        qc = get_qdrant_client()
        _visual_search = make_visual_search(
            Qwen3VLEmbedder(), QdrantVisualIndex(qc))
    except Exception as e:
        logger.warning(
            "Ramo VISUAL degradato: %s (%s). Fallback a NARRATIVE.",
            type(e).__name__, e, exc_info=True
        )
        _visual_search = None


def get_deps(role: str) -> Deps:
    """Returns a cached Deps instance for the given role, injecting shared singletons."""
    validated = parse_role(role)
    role_key = validated.value
    
    if role_key in _CACHE:
        return _CACHE[role_key]
        
    _init_shared_singletons()
    
    deps = build_default_deps(validated, llm=_llm, visual_search=_visual_search)
    _CACHE[role_key] = deps
    return deps

def get_serving_health() -> dict:
    """Returns the health status of the serving process, including resident models."""
    resident = None
    if _multiplexer is not None:
        residents = list(_multiplexer.resident())
        if residents:
            resident = residents[0]  # multiplexer max_resident=1 by default, or just take first
    
    return {
        "resident_model": resident,
        "roles_cached": list(_CACHE.keys())
    }
