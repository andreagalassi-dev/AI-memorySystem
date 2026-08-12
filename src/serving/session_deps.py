"""Dependencies for the serving session, providing shared singletons."""
import logging
import os
import concurrent.futures
import threading
from dataclasses import replace

from src.graph.pipeline import Deps, build_default_deps, parse_role

logger = logging.getLogger(__name__)

# Singletons (lazy initialization)
_llm = None
_judge_llm = None
_visual_search = None
_visual_reranker = None
_multiplexer = None
_CACHE: dict[str, Deps] = {}

# Giudice di pertinenza della pagina: scelta CHIUSA (una lettera o NESSUNA), quindi
# temperatura 0 e pochissimi token. Seed fisso: la misura dev'essere riproducibile.
JUDGE_OPTIONS = {"temperature": 0.0, "seed": 7, "num_predict": 16}


def _visual_rerank_enabled() -> bool:
    """Gate del reranker visivo: env esplicito, altrimenti soglia RAM.

    - AIKE_VISUAL_RERANK = "1"/"true"  -> SEMPRE attivo
    - AIKE_VISUAL_RERANK = "0"/"false" -> SEMPRE spento
    - assente o "auto" -> attivo SOLO se RAM totale >= AIKE_RERANK_MIN_GB (default 96).
      Se psutil non e' disponibile, gate = OFF (log).
    """
    flag = os.getenv("AIKE_VISUAL_RERANK", "auto").strip().lower()
    if flag in ("1", "true"):
        return True
    if flag in ("0", "false"):
        return False
    # "auto" (o valore non riconosciuto): decide la RAM
    try:
        import psutil
    except Exception:
        logger.info("visual reranker: OFF (psutil assente, gate RAM non valutabile)")
        return False
    min_gb = float(os.getenv("AIKE_RERANK_MIN_GB", "96"))
    total_gb = psutil.virtual_memory().total / 1e9
    on = psutil.virtual_memory().total >= min_gb * 1e9
    logger.info(
        "visual reranker: %s, RAM=%.1fGB (soglia %.1fGB)",
        "ON" if on else "OFF", total_gb, min_gb,
    )
    return on

def _page_judge_enabled() -> bool:
    """Gate del verificatore di pertinenza della pagina (ramo visivo).

    AIKE_VISUAL_PAGE_JUDGE = "1"/"true" -> acceso. Assente o qualunque altro
    valore -> SPENTO.

    Il default e' OFF perche' MISURATO in perdita sul metro del progetto. Il
    gate conta una risposta corretta se la pagina attesa e' fra quelle CITATE
    (`citation_ok` sulla lista, non sul top-1) e il ramo visivo ne mostra 3.
    Con quella definizione, sulle 34 query dei due truth set il giudice elimina
    **0** confident-wrong e ne perde **2** (V011, CV09), al costo di +12.6% di
    tempo sul carico misto. Non e' tarabile con un prompt piu' permissivo: le
    due perdite sono `NESSUNA` su casi in cui la pagina giusta era gia' mostrata
    (V011 = copertina in cima ma tavola giusta in terza posizione; CV09 = tre
    modelli 250/500/1200 e domanda ambigua), quindi allentare il giudice lo
    spegnerebbe con piu' passaggi.

    Il codice resta in piedi, testato e spento: manca il SEGNALE che lo
    renderebbe utile, cioe' distinguere una COPERTINA di catalogo da una TAVOLA
    (l'unico confident-wrong vero rimasto, V014, e' la copertina Rimoldi, e il
    giudice la conferma). Vedi `src/graph/tools/page_judge.py`, sezione sul
    limite noto: le tavole portano sempre un identificatore di figura
    (`FIG`, `Figure`, `TAV`), le copertine mai — da validare sul corpus prima
    di cablarlo.
    """
    return os.getenv("AIKE_VISUAL_PAGE_JUDGE", "0").strip().lower() in ("1", "true")


_LLM_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="LLMProxy")

_INIT_LOCK = threading.Lock()
_INFERENCE_LOCK = threading.Lock()

def _init_shared_singletons():
    global _llm, _judge_llm, _visual_search, _visual_reranker
    if _llm is not None:
        return
        
    with _INIT_LOCK:
        if _llm is not None:
            return
            
        # Initialize shared Ollama Models
        from src.graph.llm import FallbackLLM
        from src.backend.clients.ollama_client import OllamaModel
        from src.backend.config import settings
        import requests
    
        primary_llm = OllamaModel(model=settings.brain_model, think=settings.brain_think)
        fallback_llm = OllamaModel(model=settings.ollama_model)
        
        try:
            r = requests.get(f"{settings.ollama_host}/api/tags", timeout=3)
            r.raise_for_status()
            models = [m.get("name") for m in r.json().get("models", [])]
            if settings.brain_model not in models:
                logger.warning(
                    "\n=== ATTENZIONE: MODELLO PRIMARIO ASSENTE ===\n"
                    f"Il modello Ollama '{settings.brain_model}' non è installato.\n"
                    f"Esegui: ollama pull {settings.brain_model}\n"
                    "============================================"
                )
            if settings.ollama_model not in models:
                logger.warning(
                    "\n=== ATTENZIONE: FALLBACK ASSENTE ===\n"
                    f"Il modello Ollama '{settings.ollama_model}' non è installato.\n"
                    f"Esegui: ollama pull {settings.ollama_model}\n"
                    "====================================="
                )
        except Exception as e:
            logger.warning("Impossibile contattare Ollama per il probe dei modelli: %s", e)
            
        _llm = FallbackLLM(primary_llm, fallback_llm, primary_name="Ollama (Brain)", fallback_name="Ollama (Fallback)")

        # Giudice di pertinenza della pagina: stesso backend, decoding DIVERSO
        # (temperatura 0, pochissimi token: la risposta e' una lettera).
        _judge_llm = FallbackLLM(
            OllamaModel(model=settings.brain_model, think=settings.brain_think,
                        options=JUDGE_OPTIONS),
            OllamaModel(model=settings.ollama_model, options=JUDGE_OPTIONS),
            primary_name="Ollama (Judge)", fallback_name="Ollama (Judge fallback)")

        # Initialize shared Visual Search
        try:
            from src.backend.clients.qdrant_client import get_qdrant_client
            from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder
            from src.retrieval.pixelrag.index import QdrantVisualIndex
            from src.graph.tools.visual_tool import make_visual_search

            # GATE hardware: istanzia il reranker (residente) SOLO se attivo,
            # per non sprecare RAM quando spento.
            if _visual_rerank_enabled():
                from src.retrieval.pixelrag.reranker import Qwen3VLReranker
                _visual_reranker = Qwen3VLReranker()
                logger.info("visual reranker: ON (singleton residente istanziato)")
            else:
                _visual_reranker = None
                logger.info("visual reranker: OFF (reranker=None, nessun modello caricato)")

            qc = get_qdrant_client()
            _visual_search = make_visual_search(
                Qwen3VLEmbedder(), QdrantVisualIndex(qc), reranker=_visual_reranker)
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
    
    # Fase 2a — soglia di sicurezza sul ramo visivo, ATTIVA solo col reranker (i suoi
    # score sono la scala su cui e' tarata). Reranker spento -> None -> nessun gate.
    from src.graph.tools.visual_tool import VISUAL_SCORE_FLOOR
    floor = VISUAL_SCORE_FLOOR if _visual_reranker is not None else None

    # Fase 2c — verificatore di pertinenza della pagina. Cablato solo se il ramo
    # visivo esiste: senza pagine non c'e' nulla da verificare. L'LLM e' dietro
    # LockedLLMProxy (serializzazione dell'inferenza), quindi il callable NON va
    # ri-avvolto in locked_callable: il lock non e' rientrante.
    page_judge = None
    if _page_judge_enabled() and deps.visual_search is not None and _judge_llm is not None:
        from src.graph.tools.page_judge import make_page_judge
        page_judge = make_page_judge(LockedLLMProxy(_judge_llm))
        logger.info("giudice pagina (ramo visivo): ON")
    else:
        logger.info("giudice pagina (ramo visivo): OFF")

    locked_deps = replace(
        deps,
        llm=LockedLLMProxy(deps.llm) if deps.llm is not None else None,
        visual_search=locked_callable(deps.visual_search),
        visual_score_floor=floor,
        page_judge=page_judge,
        retrieve=locked_callable(deps.retrieve),
        retrieve_superseded=locked_callable(deps.retrieve_superseded),
        retrieve_deleted=locked_callable(deps.retrieve_deleted)
    )
    
    _CACHE[role_key] = locked_deps
    return locked_deps

def get_serving_health() -> dict:
    """Returns the health status of the serving process, compresi i modelli usati.
    
    resident_model rappresenta l'eventuale modello MLX caricato in memoria dal 
    processo corrente (multiplexer), che in produzione Ollama-based è tipicamente null.
    """
    from src.backend.config import settings
    
    resident = None
    if _multiplexer is not None:
        residents = list(_multiplexer.resident())
        if residents:
            resident = residents[0]  # multiplexer max_resident=1 by default, o prendi il primo
    
    return {
        "brain_model": settings.brain_model,
        "fallback_model": settings.ollama_model,
        "resident_model": resident,
        "roles_cached": list(_CACHE.keys())
    }
