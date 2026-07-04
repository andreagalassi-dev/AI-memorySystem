"""P3.5 — Orchestrazione a grafo (LangGraph) come FSM deterministica.

Cabla i pezzi gia' testati: router (P2.4) -> retrieval -> confidence (P3.2) ->
fusion diagnostica (P3.3) -> sintesi LLM (P3.4) -> guardrail (P3.1) -> finalize.
I nodi decisionali sono PURI; solo retrieval/synthesis fanno I/O, col client
INIETTATO (Deps). Fail-closed di rete: qualsiasi eccezione di Qdrant/Ollama ->
system_error -> BASSA "Sistema non disponibile". Design: docs/superpowers/specs/
2026-07-04-p35-graph-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, NamedTuple, TypedDict

from langgraph.graph import END, START, StateGraph

from src.common.roles import Role, parse_role
from src.retrieval.confidence import Signals, Thresholds, compute_margin, confidence_level
from src.retrieval.guardrail import check_answer
from src.retrieval.router import classify_query
from src.retrieval.synthesis import REFUSAL, extract_citations, strip_citations

SYS_UNAVAIL = "Sistema non disponibile. Riprova più tardi."
ACCESS_MSG = "Richiesta rifiutata: ruolo mancante o non valido."
BLOCK_MSG = ("Verifica manuale: la risposta contiene un codice non confermato "
             "dall'evidenza recuperata.")


class Evidence(NamedTuple):
    doc: str
    page: int
    lang: str
    code: str | None
    desc: str | None
    text: str | None
    qty: float | None
    price: float | None
    type: str
    status: str | None
    warnings: list[str]
    score: float
    # P4.4 — additivi CON default: le costruzioni posizionali a 12 campi
    # (P3.5) restano valide. version_status: "current" | "superseded".
    version_status: str | None = None
    doc_version: int | None = None


class GraphState(TypedDict, total=False):
    query: str
    role: str
    access_error: bool
    route: str
    code_token: str | None
    route_reason: str
    evidence: list[Evidence]
    margin: float
    has_evidence: bool
    unreliable: bool
    level: str
    agreement: str | None
    answer: str | None
    citations: list[tuple[str, int]]
    guardrail_gate: str
    system_error: bool
    obsolete_warning: bool
    fallback_done: bool
    final_answer: str
    final_level: str
    audit: list[str]


@dataclass
class Deps:
    retrieve: Callable[[str], list[Evidence]]
    synthesize: Callable[[str, list[Evidence]], str]
    catalog_lookup: Callable[[str], bool]
    thresholds: Thresholds
    cross_probe: Callable[[str], list[tuple[str, int]]] | None = None
    # P4.4 — cascade fallback sul SOSTITUITO; None = comportamento P3.5
    retrieve_superseded: Callable[[str], list[Evidence]] | None = None


def _audit(state: GraphState, msg: str) -> list[str]:
    return list(state.get("audit", [])) + [msg]


# ------------------------------------------------------------------- nodi

def router_node(state: GraphState, deps: Deps) -> dict:
    # P4.5 — difesa in profondita': il grafo e' fail-closed anche su invoke
    # DIRETTO senza passare da answer(); mai retrieval/LLM senza ruolo valido
    try:
        role = parse_role(state.get("role"))
    except (TypeError, ValueError):
        return {"access_error": True, "level": "BASSA",
                "audit": _audit(state, "ingresso: ruolo mancante o non valido "
                                       f"({state.get('role')!r})")}
    r = classify_query(state["query"])
    return {"route": r.query_type, "code_token": r.code_token,
            "route_reason": r.reason,
            "audit": _audit(state, f"router[{role.value}]: {r.reason}")}


def retrieval_node(state: GraphState, deps: Deps) -> dict:
    try:
        ev = deps.retrieve(state["query"])
        return {"evidence": ev, "audit": _audit(state, f"retrieval: {len(ev)} hit")}
    except Exception as exc:  # fail-closed: Qdrant giu'/timeout
        return {"system_error": True, "level": "BASSA",
                "audit": _audit(state, f"Sistema non disponibile (retrieval): {exc}")}


def confidence_node(state: GraphState, deps: Deps) -> dict:
    ev = state.get("evidence") or []
    scores = [e.score for e in ev]
    is_code = state.get("route") == "code"
    has_ev = (bool(ev) and ev[0].score > 0) if is_code else bool(ev)
    top = ev[0] if ev else None
    unreliable = bool(top and (top.status == "degraded" or "ocr_source" in top.warnings))
    margin = compute_margin(scores)
    rep = confidence_level(
        Signals("PASS", has_ev, margin, unreliable, agreement=None), deps.thresholds)
    return {"margin": margin, "has_evidence": has_ev, "unreliable": unreliable,
            "level": rep.level.value, "audit": _audit(state, f"confidence: {rep.reason}")}


def fusion_node(state: GraphState, deps: Deps) -> dict:
    """Cross-probe visivo LAZY (solo MEDIA) -> agreement DIAGNOSTICO (non promuove).
    Non-fatale: un errore azzera il segnale, non ferma il grafo (P3.3)."""
    if deps.cross_probe is None or state.get("level") != "MEDIA" or not state.get("evidence"):
        return {"agreement": None}
    from src.retrieval.fusion import page_agreement
    try:
        top = state["evidence"][0]
        vis = deps.cross_probe(state["query"])
        a = page_agreement((top.doc, top.page), vis, delta=1, topk=3)
        return {"agreement": a, "audit": _audit(state, f"fusion(diagnostica): accordo={a}")}
    except Exception as exc:
        return {"agreement": None, "audit": _audit(state, f"fusion cross-probe fallito: {exc}")}


def synthesis_node(state: GraphState, deps: Deps) -> dict:
    try:
        out = deps.synthesize(state["query"], state.get("evidence") or [])
        return {"answer": out, "citations": extract_citations(out),
                "audit": _audit(state, "synthesis: risposta generata")}
    except Exception as exc:  # fail-closed: Ollama giu'/timeout
        return {"system_error": True, "level": "BASSA",
                "audit": _audit(state, f"Sistema non disponibile (synthesis): {exc}")}


def guardrail_node(state: GraphState, deps: Deps) -> dict:
    payloads = [e._asdict() for e in state.get("evidence") or []]
    # il guardrail legge le AFFERMAZIONI, non le citazioni (nomi-doc code-shaped)
    rep = check_answer(strip_citations(state.get("answer") or ""),
                       payloads, deps.catalog_lookup)
    return {"guardrail_gate": rep.gate,
            "audit": _audit(state, f"guardrail: {rep.reason}")}


def fallback_node(state: GraphState, deps: Deps) -> dict:
    """P4.4 — Cascade fallback sul SOSTITUITO (solo se current -> BASSA).

    Le evidence trovate SOVRASCRIVONO quelle povere del current: mai
    contesto misto v1/v2 all'LLM. Fail-closed come retrieval_node."""
    try:
        ev = deps.retrieve_superseded(state["query"])
        return {"evidence": ev, "obsolete_warning": True, "fallback_done": True,
                "audit": _audit(state, f"fallback su SOSTITUITO: {len(ev)} hit")}
    except Exception as exc:
        return {"system_error": True, "level": "BASSA", "fallback_done": True,
                "audit": _audit(state, f"Sistema non disponibile (fallback): {exc}")}


def refuse_node(state: GraphState, deps: Deps) -> dict:
    return {"answer": REFUSAL, "audit": _audit(state, "livello BASSA -> rifiuto")}


def reconcile(state: GraphState) -> dict:
    """Riconciliazione finale pura: accesso / system_error / rifiuto / guardrail."""
    if state.get("access_error"):
        return {"final_answer": ACCESS_MSG, "final_level": "BASSA"}
    if state.get("system_error"):
        return {"final_answer": SYS_UNAVAIL, "final_level": "BASSA"}
    if state.get("level") == "BASSA":
        return {"final_answer": REFUSAL, "final_level": "BASSA"}
    answer_text = state.get("answer") or ""
    # seconda linea "non lo so": l'LLM ha rifiutato pur con retrieval MEDIA/ALTA
    if REFUSAL in answer_text:
        return {"final_answer": REFUSAL, "final_level": "BASSA"}
    gate = state.get("guardrail_gate", "PASS")
    if gate == "BLOCK":
        return {"final_answer": BLOCK_MSG, "final_level": "BASSA"}
    if gate == "VERIFY":
        out = {"final_answer": f"{answer_text}\n(verifica consigliata)",
               "final_level": "MEDIA"}
    else:
        out = {"final_answer": answer_text, "final_level": state.get("level", "MEDIA")}
    # P4.4 — SPEC §E: risposta da fonte sostituita -> avviso esplicito e
    # mai ALTA (cap a MEDIA, come il gate VERIFY: correttezza-prima)
    if state.get("obsolete_warning"):
        out["final_answer"] += ("\nATTENZIONE: risposta basata su documentazione "
                                "SOSTITUITA (obsoleta). Verificare la versione corrente.")
        if out["final_level"] == "ALTA":
            out["final_level"] = "MEDIA"
    return out


def finalize_node(state: GraphState, deps: Deps) -> dict:
    out = reconcile(state)
    return {**out, "audit": _audit(state, f"finalize: {out['final_level']}")}


# ------------------------------------------------------------------- grafo

def _after_router(state: GraphState) -> str:
    return "finalize" if state.get("access_error") else "retrieval"


def _after_retrieval(state: GraphState) -> str:
    return "finalize" if state.get("system_error") else "confidence"


def _after_confidence(state: GraphState, has_fallback: bool = False) -> str:
    if state.get("level") != "BASSA":
        return "fusion"
    # P4.4 — cascade: BASSA sulla via current -> UNA sola discesa sul sostituito
    if has_fallback and not state.get("fallback_done"):
        return "fallback"
    return "refuse"


def _after_fallback(state: GraphState) -> str:
    return "finalize" if state.get("system_error") else "confidence"


def _after_synthesis(state: GraphState) -> str:
    return "finalize" if state.get("system_error") else "guardrail"


def build_graph(deps: Deps):
    """Compila la FSM. I nodi sono chiusi su `deps` (client iniettato)."""
    def n(fn):
        return lambda state: fn(state, deps)

    g = StateGraph(GraphState)
    g.add_node("router", n(router_node))
    g.add_node("retrieval", n(retrieval_node))
    g.add_node("confidence", n(confidence_node))
    g.add_node("fallback", n(fallback_node))
    g.add_node("fusion", n(fusion_node))
    g.add_node("synthesis", n(synthesis_node))
    g.add_node("guardrail", n(guardrail_node))
    g.add_node("refuse", n(refuse_node))
    g.add_node("finalize", n(finalize_node))

    g.add_edge(START, "router")
    g.add_conditional_edges("router", _after_router,
                            {"finalize": "finalize", "retrieval": "retrieval"})
    g.add_conditional_edges("retrieval", _after_retrieval,
                            {"finalize": "finalize", "confidence": "confidence"})
    has_fb = deps.retrieve_superseded is not None
    g.add_conditional_edges("confidence",
                            lambda s: _after_confidence(s, has_fallback=has_fb),
                            {"refuse": "refuse", "fusion": "fusion",
                             "fallback": "fallback"})
    g.add_conditional_edges("fallback", _after_fallback,
                            {"finalize": "finalize", "confidence": "confidence"})
    g.add_edge("fusion", "synthesis")
    g.add_conditional_edges("synthesis", _after_synthesis,
                            {"finalize": "finalize", "guardrail": "guardrail"})
    g.add_edge("refuse", "finalize")
    g.add_edge("guardrail", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


def answer(query: str, role: Role | str, deps: Deps | None = None) -> dict:
    """Entry point: esegue il grafo. Con deps=None usa i client reali.

    P4.5 — `role` OBBLIGATORIO e tipizzato (SPEC §F.3): TypeError su
    None/non-str, ValueError su ruoli fuori vocabolario. Mai default.
    """
    validated = parse_role(role)
    return build_graph(deps or _default_deps(validated)).invoke(
        {"query": query, "role": validated.value})


def _default_deps(role: Role) -> Deps:
    """Wiring di produzione: Qdrant (routed_rr) + Ollama. Import lazy."""
    from qdrant_client import models

    from src.backend.clients.ollama_client import get_ollama_client
    from src.backend.clients.qdrant_client import get_qdrant_client
    from src.retrieval.confidence import CALIBRATED_V1
    from src.retrieval.rerank import RERANK_TOPK, _reranker
    from src.retrieval.search import access_filter, search
    from src.retrieval.synthesis import synthesize as _synth

    qc = get_qdrant_client()
    oc = get_ollama_client()

    def _to_ev(hit, score) -> Evidence:
        p = hit.payload
        return Evidence(p.get("doc"), p.get("page"), p.get("lang"), p.get("code"),
                        p.get("desc"), p.get("text"), p.get("qty"), p.get("price"),
                        p.get("type"), p.get("status"), p.get("warnings") or [], score,
                        p.get("version_status"), p.get("doc_version"))

    def _retrieve(version_status: str):
        # P4.4/P4.5 — stessa logica routed_rr su scope diversi, SEMPRE dentro
        # il perimetro del ruolo: AND(versione, ruolo) — anche il fallback
        # sull'obsoleto resta filtrato per ruolo
        vf = access_filter(version_status, role)

        def retrieve(query: str) -> list[Evidence]:
            route = classify_query(query)
            if route.query_type == "code":
                hits = search(qc, query, "routed", limit=10, query_filter=vf)
                return [_to_ev(h, h.score) for h in hits]
            # natural/mixed: reranker SIGMOID come score di produzione (P3.2 §3)
            import torch
            base = search(qc, query, "routed", limit=RERANK_TOPK, query_filter=vf)
            if not base:
                return []
            tok, model, device = _reranker()
            pairs = [(query, h.payload.get("text") or h.payload.get("desc") or "")
                     for h in base]
            batch = tok(pairs, padding=True, truncation=True, max_length=512,
                        return_tensors="pt").to(device)
            with torch.no_grad():
                probs = torch.sigmoid(
                    model(**batch).logits.squeeze(-1).float()).cpu().tolist()
            if not isinstance(probs, list):
                probs = [probs]
            ranked = sorted(zip(probs, base), key=lambda t: -t[0])
            return [_to_ev(h, s) for s, h in ranked[:10]]

        return retrieve

    def catalog_lookup(key: str) -> bool:
        r = qc.count("aike_chunks", exact=True, count_filter=models.Filter(
            must=[models.FieldCondition(key="code", match=models.MatchValue(value=key))]))
        return r.count > 0

    def synthesize(query: str, ev: list[Evidence]) -> str:
        return _synth(oc, query, [e._asdict() for e in ev], model=None)

    return Deps(retrieve=_retrieve("current"), synthesize=synthesize,
                catalog_lookup=catalog_lookup, thresholds=CALIBRATED_V1,
                retrieve_superseded=_retrieve("superseded"))
