"""Dependencies for the serving session, providing shared singletons."""
import logging
import os
import concurrent.futures
import threading
from dataclasses import replace

from src.graph.pipeline import Deps, build_default_deps, parse_role
from src.graph.tools.page_judge import JUDGE_MODE_REFUSE, JUDGE_MODE_REORDER

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


def _page_reorder_enabled() -> bool:
    """Gate del RIORDINO delle pagine visive (l'altro uso del giudice).

    AIKE_VISUAL_PAGE_REORDER = "0"/"false" -> spento. Assente o altro -> ACCESO.

    Default ON perche' MISURATO in guadagno, e con una perdita strutturalmente
    impossibile: `promote_chosen` fa `out.insert(0, out.pop(idx))`, cioe' sposta
    un elemento senza toglierne o aggiungerne, quindi l'INSIEME delle pagine
    mostrate non puo' cambiare e `recall@3`/`recall@5` non possono scendere.
    Misura su `data/eval/eval_visual_v0.jsonl` (15 query), confermata da due
    esecuzioni indipendenti: recall@1 0.333 -> 0.467, MRR 0.478 -> 0.556,
    recall@3 e @5 identici cifra per cifra. Costo: +0.4s p50 sul ramo visivo.
    E' l'opposto del VETO (`AIKE_VISUAL_PAGE_JUDGE`), che resta OFF perche'
    misurato in perdita: quello puo' togliere una risposta, questo no.

    Interruttore SEPARATO da `AIKE_VISUAL_PAGE_JUDGE`, perche' i due usi hanno
    poteri diversi e vanno decisi separatamente: il veto puo' togliere una
    risposta, il riordino no. In riordino il giudice non toglie e non aggiunge
    nulla — le stesse pagine restano a schermo, cambia solo quale sta in cima —
    quindi non puo' peggiorare `recall@3`/`recall@5`, solo il primo posto.
    Vedi `src/graph/tools/page_judge.py`, "Due usi dello stesso strumento".
    """
    return os.getenv("AIKE_VISUAL_PAGE_REORDER", "1").strip().lower() not in ("0", "false")


def _page_judge_mode() -> str:
    """Modalita' effettiva del giudice quando e' cablato.

    Il giudice viene interrogato UNA volta sola: le due modalita' sono due
    letture dello stesso verdetto, non due chiamate. Se qualcuno accende
    entrambi gli interruttori vince il VETO, l'unica delle due che puo'
    impedire una risposta sbagliata.
    """
    if _page_judge_enabled():
        return JUDGE_MODE_REFUSE
    return JUDGE_MODE_REORDER if _page_reorder_enabled() else JUDGE_MODE_REFUSE


def _web_cascade_enabled() -> bool:
    """Epica H — interruttore della cascata web. DEFAULT OFF, e non per timidezza.

    AIKE_WEB_CASCADE = "1"/"true" -> accesa. Assente o altro -> SPENTA.

    La ragione del default sta tutta in una frase: **ogni misura esistente in
    questo repo e' stata presa col web spento**. Il golden gate, le baseline
    per-lingua, i numeri di recall, il conteggio dei WRONG_ALTA: tutti
    fotografano un sistema che, quando il corpus non sa, dice "non lo so".
    Accendere la cascata di default sposterebbe il metro sotto i piedi del
    metro — gli item che oggi sono rifiuti diventerebbero risposte WEB, e
    nessuno saprebbe piu' se un confronto fra due run misura il codice o
    l'interruttore. Chi accende, accende apposta, e l'audit lo scrive
    (`run_config.effective_config`).

    Interruttore SEPARATO da `AIKE_WEB_SEARCH` (che sceglie il MOTORE:
    off/searxng/frozen) perche' sono due decisioni diverse: "esiste un motore
    raggiungibile" e "il grafo puo' usarlo per rispondere". Servono entrambe:
    con il motore spento questa resta una dichiarazione d'intenti innocua.
    """
    return os.getenv("AIKE_WEB_CASCADE", "0").strip().lower() in ("1", "true")


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
            
        # Initialize shared LLM Models (Ollama locale, API Cloud o Mock)
        from src.graph.llm import FallbackLLM, APIModel, MockModel
        from src.backend.clients.ollama_client import OllamaModel
        from src.backend.config import settings
        import requests

        provider = (settings.llm_provider or "auto").lower()

        # 1. Mock esplicito
        if provider == "mock":
            logger.info("Inizializzazione LLM: Provider Mock / Demo attivo.")
            _llm = MockModel()
            _judge_llm = MockModel()

        # 2. OpenAI / compatibili
        elif provider in ("openai", "openrouter", "groq") or (provider == "auto" and settings.openai_api_key):
            logger.info("Inizializzazione LLM: Provider API OpenAI attivo (%s).", settings.openai_model)
            _llm = APIModel(
                provider="openai",
                api_key=settings.openai_api_key,
                model_name=settings.openai_model,
                base_url=settings.openai_base_url,
            )
            _judge_llm = APIModel(
                provider="openai",
                api_key=settings.openai_api_key,
                model_name=settings.openai_model,
                base_url=settings.openai_base_url,
                temperature=0.0,
                max_tokens=64,
            )

        # 3. Google Gemini
        elif provider == "gemini" or (provider == "auto" and settings.gemini_api_key):
            logger.info("Inizializzazione LLM: Provider API Google Gemini attivo (%s).", settings.gemini_model)
            _llm = APIModel(
                provider="gemini",
                api_key=settings.gemini_api_key,
                model_name=settings.gemini_model,
            )
            _judge_llm = APIModel(
                provider="gemini",
                api_key=settings.gemini_api_key,
                model_name=settings.gemini_model,
                temperature=0.0,
                max_tokens=64,
            )

        # 4. Anthropic Claude
        elif provider == "anthropic" or (provider == "auto" and settings.anthropic_api_key):
            logger.info("Inizializzazione LLM: Provider API Anthropic attivo (%s).", settings.anthropic_model)
            _llm = APIModel(
                provider="anthropic",
                api_key=settings.anthropic_api_key,
                model_name=settings.anthropic_model,
            )
            _judge_llm = APIModel(
                provider="anthropic",
                api_key=settings.anthropic_api_key,
                model_name=settings.anthropic_model,
                temperature=0.0,
                max_tokens=64,
            )

        # 5. Ollama locale (default tradizionale) con graceful fallback a MockModel
        else:
            primary_llm = OllamaModel(model=settings.brain_model, think=settings.brain_think)
            fallback_llm = OllamaModel(model=settings.ollama_model)
            ollama_reachable = False

            try:
                r = requests.get(f"{settings.ollama_host}/api/tags", timeout=2)
                r.raise_for_status()
                models = [m.get("name") for m in r.json().get("models", [])]
                ollama_reachable = True
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
                logger.warning("Ollama non raggiungibile (%s). Attivato fallback su MockModel.", e)

            if ollama_reachable:
                _llm = FallbackLLM(primary_llm, fallback_llm, primary_name="Ollama (Brain)", fallback_name="Ollama (Fallback)")
                _judge_llm = FallbackLLM(
                    OllamaModel(model=settings.brain_model, think=settings.brain_think, options=JUDGE_OPTIONS),
                    OllamaModel(model=settings.ollama_model, options=JUDGE_OPTIONS),
                    primary_name="Ollama (Judge)", fallback_name="Ollama (Judge fallback)"
                )
            else:
                _llm = MockModel(prefix="[OLLAMA OFFLINE - DEMO MOCK] ")
                _judge_llm = MockModel(prefix="[OLLAMA OFFLINE] ")

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
    
    # Epica H — motore di ricerca online: costruito SOLO se la cascata e'
    # accesa E il motore e' configurato. Fail-safe come gli altri rami: se la
    # cassetta manca o l'URL e' storto si degrada a None (nessun ramo web),
    # mai un crash all'avvio della sessione.
    web_search = None
    if _web_cascade_enabled():
        try:
            from src.retrieval.websearch import build_web_search_from_env
            web_search = build_web_search_from_env()
        except Exception as e:
            logger.warning("Cascata web degradata: %s (%s). Ramo web spento.",
                           type(e).__name__, e)
            web_search = None
        logger.info("cascata web: %s (motore=%s)",
                    "ON" if web_search is not None else "OFF (nessun motore)",
                    type(web_search).__name__ if web_search else None)
    else:
        logger.info("cascata web: OFF (AIKE_WEB_CASCADE non attivo)")

    deps = build_default_deps(validated, llm=_llm, visual_search=_visual_search,
                              web_search=web_search)
    
    # Fase 2a — soglia di sicurezza sul ramo visivo, ATTIVA solo col reranker (i suoi
    # score sono la scala su cui e' tarata). Reranker spento -> None -> nessun gate.
    from src.graph.tools.visual_tool import VISUAL_SCORE_FLOOR
    floor = VISUAL_SCORE_FLOOR if _visual_reranker is not None else None

    # Fase 2c — verificatore di pertinenza della pagina. Cablato solo se il ramo
    # visivo esiste: senza pagine non c'e' nulla da verificare. L'LLM e' dietro
    # LockedLLMProxy (serializzazione dell'inferenza), quindi il callable NON va
    # ri-avvolto in locked_callable: il lock non e' rientrante.
    page_judge = None
    judge_mode = _page_judge_mode()
    judge_wanted = _page_judge_enabled() or _page_reorder_enabled()
    if judge_wanted and deps.visual_search is not None and _judge_llm is not None:
        from src.graph.tools.page_judge import make_page_judge
        page_judge = make_page_judge(LockedLLMProxy(_judge_llm))
        logger.info("giudice pagina (ramo visivo): ON, modalita'=%s", judge_mode)
    else:
        logger.info("giudice pagina (ramo visivo): OFF")

    locked_deps = replace(
        deps,
        llm=LockedLLMProxy(deps.llm) if deps.llm is not None else None,
        visual_search=locked_callable(deps.visual_search),
        visual_score_floor=floor,
        page_judge=page_judge,
        page_judge_mode=judge_mode,
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
