"""Configurazione effettiva di un run di misura: caricarla e DICHIARARLA.

Perche' esiste questo modulo
----------------------------
Un numero di qualita' senza la configurazione che l'ha prodotto non e' una
misura: e' un aneddoto. Gli script di misura (`scripts/golden_gate.py`,
`scripts/smoke_e2e.py`) NON caricavano il `.env` — lo faceva solo
`src/ui/chainlit_app.py` — quindi giravano con `AIKE_VISUAL_RERANK` assente,
cadevano nel gate "auto" (soglia RAM), spegnevano il reranker visivo e con esso
il pavimento anti-confident-wrong (`visual_score_floor`, iniettato solo col
reranker attivo, vedi `src/serving/session_deps.py`). Risultato: il gate
misurava l'unica configurazione SENZA il pavimento, diversa da quella che
l'utente usa in UI, e nulla nell'output lo diceva.

Le due funzioni pubbliche coprono le due meta' del problema:
  - `load_runtime_env()`  -> carica lo STESSO ambiente della produzione,
    con la stessa precedenza di `chainlit_app.py` (`load_dotenv()` senza
    override: le variabili gia' in `os.environ` vincono sul file);
  - `effective_config()` / `format_config_banner()` -> fotografano la
    configurazione risultante in un blocco stampabile e serializzabile, da
    persistere negli audit perche' un audit vecchio resti interpretabile.
"""

from __future__ import annotations

import os
from typing import Any


def load_runtime_env() -> str | None:
    """Carica il `.env` come fa la produzione. Ritorna il path caricato (o None).

    Precedenza identica a `src/ui/chainlit_app.py:19`: `load_dotenv()` con i
    default, quindi le variabili gia' esportate nell'ambiente NON vengono
    sovrascritte dal file.
    """
    try:
        from dotenv import find_dotenv, load_dotenv
    except Exception:  # python-dotenv assente: nessun caricamento, nessun crash
        return None

    path = find_dotenv(usecwd=True)
    if not path:
        # fallback: risalita dalla posizione di questo file (repo root)
        candidate = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            ".env",
        )
        path = candidate if os.path.exists(candidate) else ""
    if not path:
        load_dotenv()
        return None
    load_dotenv(path)
    return path


def visual_rerank_decision() -> tuple[bool, str]:
    """(reranker visivo attivo?, PERCHE'). Il booleano viene dall'unica fonte
    di verita' (`session_deps._visual_rerank_enabled`); qui si ricostruisce solo
    la motivazione leggibile."""
    from src.serving.session_deps import _visual_rerank_enabled

    enabled = _visual_rerank_enabled()
    raw = os.getenv("AIKE_VISUAL_RERANK")
    flag = (raw or "auto").strip().lower()

    if flag in ("1", "true", "0", "false"):
        return enabled, f"env AIKE_VISUAL_RERANK={raw!r} (esplicito)"

    min_gb = os.getenv("AIKE_RERANK_MIN_GB", "96")
    try:
        import psutil

        total_gb = psutil.virtual_memory().total / 1e9
        ram = f"RAM totale {total_gb:.1f}GB, soglia {min_gb}GB"
    except Exception:
        ram = f"RAM non valutabile (psutil assente), soglia {min_gb}GB"
    origin = "assente" if raw is None else repr(raw)
    return enabled, f"gate auto (AIKE_VISUAL_RERANK {origin}) — {ram}"


def effective_config(deps: Any | None = None) -> dict:
    """Blocco `config` del run: JSON-serializzabile, stampabile, persistibile.

    Con `deps` (quelle davvero costruite dal run) i campi del ramo visivo sono
    OSSERVATI, non previsti: se il reranker esplode in init, l'env dice ON ma il
    pavimento reale e' None e l'audit deve dire la verita'.
    """
    from src.backend.config import settings
    from src.graph.tools.visual_tool import VISUAL_SCORE_FLOOR
    from src.graph.tools.page_judge import JUDGE_MODE_REFUSE, JUDGE_MODE_REORDER
    from src.serving.session_deps import (_page_judge_enabled, _page_judge_mode,
                                          _page_reorder_enabled)

    # PRIMA il .env, POI la fotografia: leggere l'ambiente prima di caricarlo
    # e' esattamente il bug che questo modulo esiste per chiudere.
    env_file = load_runtime_env()
    rerank_on, rerank_why = visual_rerank_decision()

    if deps is not None:
        floor = getattr(deps, "visual_score_floor", None)
        floor_source = "osservato"
        visual_available = getattr(deps, "visual_search", None) is not None
        judge_on = getattr(deps, "page_judge", None) is not None
        judge_mode = getattr(deps, "page_judge_mode", JUDGE_MODE_REFUSE)
        judge_source = "osservato"
    else:
        floor = VISUAL_SCORE_FLOOR if rerank_on else None
        floor_source = "previsto"
        visual_available = None
        judge_on = _page_judge_enabled() or _page_reorder_enabled()
        judge_mode = _page_judge_mode()
        judge_source = "previsto"
    raw_judge = os.getenv("AIKE_VISUAL_PAGE_JUDGE")
    raw_reorder = os.getenv("AIKE_VISUAL_PAGE_REORDER")
    judge_reason = (f"env AIKE_VISUAL_PAGE_JUDGE={raw_judge!r}" if raw_judge is not None
                    else "env AIKE_VISUAL_PAGE_JUDGE assente (default OFF)")
    judge_reason += (f", AIKE_VISUAL_PAGE_REORDER={raw_reorder!r}" if raw_reorder is not None
                     else ", AIKE_VISUAL_PAGE_REORDER assente (default ON)")

    try:
        from src.ingestion.embedding import COLLECTION as TEXT_COLLECTION
    except Exception:
        TEXT_COLLECTION = None
    try:
        from src.retrieval.pixelrag.index import DEFAULT_VISUAL_COLLECTION
    except Exception:
        DEFAULT_VISUAL_COLLECTION = None

    from src.retrieval.rerank import RERANK_TOPK_DEFAULT, RERANK_TOPK_ENV, rerank_topk

    topk = rerank_topk()
    topk_raw = os.getenv(RERANK_TOPK_ENV)
    topk_reason = (f"env {RERANK_TOPK_ENV}={topk_raw!r}" if topk_raw is not None
                   else f"env {RERANK_TOPK_ENV} assente (default {RERANK_TOPK_DEFAULT})")

    return {
        "env_file": env_file,
        # Pool RRF dato al cross-encoder TESTUALE. Cambiarlo cambia sia la
        # qualita' sia la latenza (il cross-encoder gira su ogni candidato):
        # un audit che non lo dichiara non e' confrontabile con gli altri.
        "rerank_topk": {"value": topk, "reason": topk_reason},
        "visual_rerank": {"enabled": rerank_on, "reason": rerank_why},
        "visual_score_floor": floor,
        "visual_score_floor_source": floor_source,
        "visual_search_available": visual_available,
        # `enabled` = il giudice viene interrogato. NON basta: i suoi due usi
        # hanno poteri diversi e un audit che li confonde mente. `veto` puo'
        # TOGLIERE una risposta, `reorder` puo' solo cambiare quale pagina sta
        # in cima. Vanno letti separatamente.
        "visual_page_judge": {"enabled": judge_on, "mode": judge_mode,
                              "veto": judge_mode == JUDGE_MODE_REFUSE and judge_on,
                              "reorder": judge_mode == JUDGE_MODE_REORDER and judge_on,
                              "source": judge_source, "reason": judge_reason},
        "llm": {
            "brain_model": settings.brain_model,
            "brain_think": settings.brain_think,
            "fallback_model": settings.ollama_model,
            "ollama_host": settings.ollama_host,
        },
        "qdrant": {
            "url": f"http://{settings.qdrant_host}:{settings.qdrant_port}",
            "collections": {
                "text": TEXT_COLLECTION,
                "visual": DEFAULT_VISUAL_COLLECTION,
            },
        },
        "postgres": {
            "host": settings.postgres_host,
            "port": settings.postgres_port,
            "db": settings.postgres_db,
        },
    }


def format_config_banner(cfg: dict) -> str:
    """Il blocco che va stampato IN TESTA al run (e incollato nei report)."""
    rr = cfg["visual_rerank"]
    floor = cfg["visual_score_floor"]
    floor_txt = "NESSUNO (nessun gate anti-confident-wrong)" if floor is None else str(floor)
    llm = cfg["llm"]
    q = cfg["qdrant"]
    lines = [
        "=" * 72,
        "CONFIGURAZIONE EFFETTIVA DEL RUN",
        "=" * 72,
        f"  .env caricato        : {cfg.get('env_file') or 'NESSUNO'}",
        f"  rerank_topk (testo)  : {cfg['rerank_topk']['value']}  [{cfg['rerank_topk']['reason']}]",
        f"  reranker visivo      : {'ON' if rr['enabled'] else 'OFF'}  [{rr['reason']}]",
        f"  visual_score_floor   : {floor_txt}  ({cfg.get('visual_score_floor_source')})",
    ]
    pj = cfg.get("visual_page_judge")
    if pj is not None:
        # la modalita' NON e' un dettaglio: 'refuse' puo' togliere una risposta,
        # 'reorder' puo' solo cambiare quale pagina sta in cima.
        modo = {"refuse": "rifiuto (veto)",
                "reorder": "riordino (preferenza)"}.get(pj.get("mode"), pj.get("mode"))
        lines.append(
            f"  giudice pagina       : {'ON' if pj['enabled'] else 'OFF'}"
            f"  modalita'={modo}  [{pj['reason']}] ({pj['source']})"
        )
    if cfg.get("visual_search_available") is not None:
        lines.append(
            f"  ramo visivo          : {'attivo' if cfg['visual_search_available'] else 'DEGRADATO (visual_search=None)'}"
        )
    lines += [
        f"  LLM cervello         : {llm['brain_model']} (think={llm['brain_think']})",
        f"  LLM fallback         : {llm['fallback_model']}",
        f"  ollama host          : {llm['ollama_host']}",
        f"  qdrant               : {q['url']}  testo={q['collections']['text']}  visivo={q['collections']['visual']}",
        f"  postgres             : {cfg['postgres']['host']}:{cfg['postgres']['port']}/{cfg['postgres']['db']}",
        "=" * 72,
    ]
    return "\n".join(lines)
