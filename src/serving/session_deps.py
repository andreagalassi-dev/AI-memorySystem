"""Dependencies for the serving session, providing shared singletons."""
import logging
import concurrent.futures
import threading
from typing import Optional
from dataclasses import replace

from src.graph.pipeline import Deps, build_default_deps, parse_role

logger = logging.getLogger(__name__)

# Singletons (lazy initialization)
_llm = None
_visual_search = None
_multiplexer = None
_CACHE: dict[str, Deps] = {}

_LLM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="LLMProxy")

_INIT_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()

def _init_shared_singletons():
    global _llm, _visual_search
    if _llm is not None:
        return
        
    with _INIT_LOCK:
        if _llm is not None:
            return
            
        # Initialize shared MLXModel + Multiplexer
        from src.graph.llm import MLXModel, FallbackLLM
        from src.serving.multiplexer import ModelMultiplexer, MlxModelRuntime
        from src.backend.clients.ollama_client import OllamaModel
        from src.backend.config import settings
        import requests
    
        global _multiplexer
        mlx_runtime = MlxModelRuntime(default_num_ctx=settings.local_ctx_cap)
        _multiplexer = ModelMultiplexer(mlx_runtime, max_resident=1, ctx_cap=settings.local_ctx_cap)
        
        primary_llm = MLXModel(settings.logic_model, _multiplexer)
        fallback_llm = OllamaModel()
        
        try:
            r = requests.get(f"{settings.ollama_host}/api/tags", timeout=3)
            r.raise_for_status()
            models = [m.get("name") for m in r.json().get("models", [])]
            if settings.ollama_model not in models:
                logger.warning(
                    "\n=== ATTENZIONE: FALLBACK ASSENTE ===\n"
                    f"Il modello Ollama '{settings.ollama_model}' non è installato.\n"
                    f"Esegui: ollama pull {settings.ollama_model}\n"
                    "====================================="
                )
        except Exception as e:
            logger.warning("Impossibile contattare Ollama per il probe del fallback: %s", e)
            
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

class LockedLLMProxy:
    def __init__(self, target):
        self.target = target
        
    def generate(self, system: str, user: str) -> str:
        with _INFERENCE_LOCK:
            future = _LLM_EXECUTOR.submit(self.target.generate, system, user)
            return future.result()

def locked_callable(target):
    if target is None:
        return None
    def wrapper(*args, **kwargs):
        with _INFERENCE_LOCK:
            return target(*args, **kwargs)
    return wrapper

def get_deps(role: str) -> Deps:
    """Returns a cached Deps instance for the given role, injecting shared singletons."""
    validated = parse_role(role)
    role_key = validated.value
    
    if role_key in _CACHE:
        return _CACHE[role_key]
        
    _LLM_EXECUTOR.submit(_init_shared_singletons).result()
    
    deps = build_default_deps(validated, llm=_llm, visual_search=_visual_search)
    
    locked_deps = replace(
        deps,
        llm=LockedLLMProxy(deps.llm) if deps.llm is not None else None,
        visual_search=locked_callable(deps.visual_search),
        retrieve=locked_callable(deps.retrieve),
        retrieve_superseded=locked_callable(deps.retrieve_superseded),
        retrieve_deleted=locked_callable(deps.retrieve_deleted)
    )
    
    _CACHE[role_key] = locked_deps
    return locked_deps

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
