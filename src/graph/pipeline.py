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
from src.graph.llm import LanguageModel
from src.graph.render import SYSTEM_RENDER, build_render_prompt, verify_price_grounding
from src.graph.tools.sql_tool import LookupRow, SqlExecutor, SqlParams, extract_params
from src.graph.tools.visual_tool import format_visual_answer, visual_citations
from src.retrieval.pixelrag.index import PageHit
from src.retrieval.confidence import (Signals, Thresholds, compute_margin,
                                      confidence_level, group_scores)
from src.retrieval.guardrail import check_answer
from src.retrieval.router import classify_query, derive_intent
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
    is_assembly: bool = False


class GraphState(TypedDict, total=False):
    query: str
    role: str
    access_error: bool
    route: str
    code_token: str | None
    route_reason: str
    # Fase 1.3 — intent d'instradamento (STRUCTURED|VISUAL|NARRATIVE) + ramo STRUCTURED
    intent: str
    sql_params: SqlParams
    sql_rows: list[LookupRow]
    # Fase 1.3 — ramo VISUAL: pagine PixelRAG (audit/provenienza)
    visual_pages: list[PageHit]
    expansion_terms: list[str]
    evidence: list[Evidence]
    children_evidence: list[Evidence]
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
    deleted_warning: bool
    fallback_superseded_done: bool
    fallback_deleted_done: bool
    final_answer: str
    final_level: str
    audit: list[str]


@dataclass
class Deps:
    # retrieve/retrieve_superseded: chiamate con (query) oppure, se il nodo
    # expansion ha prodotto termini (debito §2), con (query, expansion_terms).
    # Con expand=None il contratto resta quello P3.5/P4.4 a un argomento.
    retrieve: Callable[..., tuple[list[Evidence], list[Evidence]]]
    synthesize: Callable[[str, list[Evidence]], str]
    catalog_lookup: Callable[[str], bool]
    thresholds: Thresholds
    cross_probe: Callable[[str], list[tuple[str, int]]] | None = None
    # P4.4 — cascade fallback sul SOSTITUITO
    retrieve_superseded: Callable[..., tuple[list[Evidence], list[Evidence]]] | None = None
    # P7 — cascade fallback sull'ELIMINATO (Livello 3)
    retrieve_deleted: Callable[..., tuple[list[Evidence], list[Evidence]]] | None = None
    # Debito §2 — Query Expansion (solo route natural); None = nodo no-op
    expand: Callable[[str], list[str]] | None = None
    # Fase 1.3 — ramo STRUCTURED: LLM (render) + esecutore SQL sicuro. Con uno dei
    # due None il ramo NON e' cablato e l'intent STRUCTURED degrada a NARRATIVE.
    llm: LanguageModel | None = None
    sql_query: SqlExecutor | None = None
    # Fase 1.3 — ramo VISUAL: binder PixelRAG (query, role) -> pagine. Con None il
    # ramo NON e' cablato e l'intent VISUAL degrada al RAG testuale (NARRATIVE).
    visual_search: Callable[[str, Role], list[PageHit]] | None = None


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
    intent = derive_intent(r, state["query"])
    return {"route": r.query_type, "code_token": r.code_token,
            "route_reason": r.reason, "intent": intent.value,
            "audit": _audit(state, f"router[{role.value}]: {r.reason} -> {intent.value}")}


def expansion_node(state: GraphState, deps: Deps) -> dict:
    """Debito §2 — Query Expansion, SOLO route natural, NON-fatale.

    Precedente: fusion_node. L'espansione e' ottimizzazione di recall, non di
    correttezza: se il dizionario non e' caricabile si degrada al comportamento
    senza espansione, con riga di audit (mai bloccare la risposta per questo).
    La query in stato NON viene mai riscritta: sintesi, guardrail e reranker
    vedono la domanda originale dell'utente."""
    if deps.expand is None or state.get("route") != "natural":
        return {}
    try:
        terms = deps.expand(state["query"])
    except Exception as exc:
        return {"expansion_terms": [],
                "audit": _audit(state, f"expansion NON-FATALE fallita: {exc}")}
    return {"expansion_terms": terms,
            "audit": _audit(state, f"expansion: +{len(terms)} termini {terms}"
                                   if terms else "expansion: nessun termine")}


def _retrieve_with_terms(fn: Callable, state: GraphState) -> tuple[list[Evidence], list[Evidence]]:
    """Contratto Deps: (query, terms) solo se l'espansione ha prodotto termini."""
    terms = state.get("expansion_terms") or []
    return fn(state["query"], terms) if terms else fn(state["query"])


def retrieval_node(state: GraphState, deps: Deps) -> dict:
    try:
        ev, children = _retrieve_with_terms(deps.retrieve, state)
        return {"evidence": ev, "children_evidence": children, 
                "audit": _audit(state, f"retrieval: {len(ev)} hit, {len(children)} children")}
    except Exception as exc:  # fail-closed: Qdrant giu'/timeout
        return {"system_error": True, "level": "BASSA",
                "audit": _audit(state, f"Sistema non disponibile (retrieval): {exc}")}


def _row_to_evidence(row: LookupRow, code: str | None, campo: str) -> Evidence:
    """Fase 1.3 — mappa una riga SQL su Evidence, cosi' il ramo STRUCTURED converge
    su guardrail/finalize INVARIATI. Il codice del listino diventa evidence-code:
    e' la base contro cui il guardrail P3.1 verifica i codici citati dall'LLM."""
    return Evidence(
        doc=row.doc_stem, page=row.page, lang=None, code=code,
        desc=(str(row.valore) if campo == "descrizione" and row.valore is not None else None),
        text=None, qty=None,
        price=(float(row.valore) if campo == "prezzo" and row.valore is not None else None),
        type="table_row", status="clean", warnings=[], score=1.0)


def sql_node(state: GraphState, deps: Deps) -> dict:
    """Fase 1.3 ramo STRUCTURED — Text-to-Parameters -> lookup sicuro su righe_listino.

    L'LLM non genera SQL: i parametri li estrae `sql_tool` (codice via canonical +
    campo da whitelist). Fail-closed: codice degenere/0 righe -> BASSA ('non lo so');
    eccezione di rete -> system_error, come `retrieval_node`. Su righe, sintetizza
    l'Evidence (per il guardrail) e alza il livello ad ALTA (lookup esatto sull'SSOT)."""
    try:
        params = extract_params(state.get("code_token") or "", state["query"])
    except ValueError as exc:
        return {"level": "BASSA",
                "audit": _audit(state, f"sql: codice non valido ({exc}) -> non lo so")}
    try:
        rows = deps.sql_query.run(params)
    except Exception as exc:  # fail-closed: Postgres giu'/timeout
        return {"system_error": True, "level": "BASSA",
                "audit": _audit(state, f"Sistema non disponibile (sql): {exc}")}
    if not rows:
        return {"sql_params": params, "sql_rows": [], "level": "BASSA",
                "audit": _audit(state, f"sql: 0 righe per '{params.codice_normalizzato}' "
                                       "-> non lo so")}
    ev = [_row_to_evidence(r, state.get("code_token"), params.campo) for r in rows]
    return {"sql_params": params, "sql_rows": rows, "evidence": ev, "level": "ALTA",
            "audit": _audit(state, f"sql: {len(rows)} riga/e campo={params.campo} -> ALTA")}


def render_node(state: GraphState, deps: Deps) -> dict:
    """Fase 1.3 ramo STRUCTURED — l'LLM formatta i dati GIA' letti (mai genera SQL).

    Barriera #2 (deterministica): sul campo prezzo, `verify_price_grounding` pretende
    che ogni prezzo enunciato sia un valore del DB e che almeno uno sia enunciato;
    altrimenti fail-closed -> BASSA (REFUSAL). Le citazioni sono ricavate da noi dalle
    righe (provenienza), non dal testo dell'LLM. Il guardrail codici resta a valle."""
    params: SqlParams = state["sql_params"]
    rows: list[LookupRow] = state["sql_rows"]
    try:
        out = deps.llm.generate(
            SYSTEM_RENDER,
            build_render_prompt(state["query"], state.get("code_token"), params.campo, rows))
    except Exception as exc:  # fail-closed: modello giu'/timeout
        return {"system_error": True, "level": "BASSA",
                "audit": _audit(state, f"Sistema non disponibile (render): {exc}")}
    if params.campo == "prezzo" and not verify_price_grounding(out, [r.valore for r in rows]):
        return {"level": "BASSA",
                "audit": _audit(state, "render: prezzo non ancorato ai dati DB -> non lo so")}
    citations = [(r.doc_stem, r.page) for r in rows]
    return {"answer": out, "citations": citations,
            "audit": _audit(state, "render: risposta formattata dai dati DB")}


def visual_node(state: GraphState, deps: Deps) -> dict:
    """Fase 1.3 ramo VISUAL — retrieval PixelRAG 1.1 -> puntatore deterministico.

    NESSUN LLM: un match visivo e' una similarita', non un lookup esatto; descrivere
    l'immagine rischierebbe l'allucinazione. Restituiamo un puntatore alle pagine
    (doc+pagina) — livello MEDIA, l'utente valida l'immagine a schermo. Fail-closed:
    eccezione di rete -> system_error (come `retrieval_node`); 0 pagine (incluso il
    caso in cui l'RBAC dentro `visual_search` filtra tutto) -> BASSA ('non lo so')."""
    role = parse_role(state["role"])  # gia' validato dal router; ribadito fail-closed
    try:
        pages = deps.visual_search(state["query"], role)
    except Exception as exc:  # fail-closed: Qdrant/embedder giu'/timeout
        return {"system_error": True, "level": "BASSA",
                "audit": _audit(state, f"Sistema non disponibile (visual): {exc}")}
    if not pages:
        return {"visual_pages": [], "level": "BASSA",
                "audit": _audit(state, "visual: 0 pagine (o filtrate da RBAC) -> non lo so")}
    return {"visual_pages": pages, "answer": format_visual_answer(pages),
            "citations": visual_citations(pages), "level": "MEDIA",
            "audit": _audit(state, f"visual: {len(pages)} pagina/e -> MEDIA")}


def confidence_node(state: GraphState, deps: Deps) -> dict:
    ev = state.get("evidence") or []
    is_code = state.get("route") == "code"
    has_ev = (bool(ev) and ev[0].score > 0) if is_code else bool(ev)
    top = ev[0] if ev else None
    # Debito §4: 'fuzzy_code' = codice RIPARATO in ingestion -> mai ALTA
    unreliable = bool(top and (top.status == "degraded"
                               or "ocr_source" in top.warnings
                               or "fuzzy_code" in top.warnings))
    # Debito §3 — margine sui GRUPPI-codice (max per gruppo): i duplicati
    # intra-doc dello stesso codice non sono ambiguita'. Solo il segnale
    # margine: evidence/citazioni/sintesi restano sugli hit originali.
    grouped = group_scores([(e.code, e.score) for e in ev])
    margin = compute_margin(grouped)
    rep = confidence_level(
        Signals("PASS", has_ev, margin, unreliable, agreement=None), deps.thresholds)
    return {"margin": margin, "has_evidence": has_ev, "unreliable": unreliable,
            "level": rep.level.value,
            "audit": _audit(state, f"confidence: {rep.reason} "
                                   f"({len(grouped)} gruppi-codice da {len(ev)} hit)")}


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
        # P3.5 / Hop 2: l'LLM vede evidenza + children combinati in fondo
        combined_ev = (state.get("evidence") or []) + (state.get("children_evidence") or [])
        out = deps.synthesize(state["query"], combined_ev)
        return {"answer": out, "citations": extract_citations(out),
                "audit": _audit(state, "synthesis: risposta generata")}
    except Exception as exc:  # fail-closed: Ollama giu'/timeout
        return {"system_error": True, "level": "BASSA",
                "audit": _audit(state, f"Sistema non disponibile (synthesis): {exc}")}


def guardrail_node(state: GraphState, deps: Deps) -> dict:
    combined_ev = (state.get("evidence") or []) + (state.get("children_evidence") or [])
    payloads = [e._asdict() for e in combined_ev]
    # il guardrail legge le AFFERMAZIONI, non le citazioni (nomi-doc code-shaped)
    rep = check_answer(strip_citations(state.get("answer") or ""),
                       payloads, deps.catalog_lookup)
    return {"guardrail_gate": rep.gate,
            "audit": _audit(state, f"guardrail: {rep.reason}")}


def fallback_superseded_node(state: GraphState, deps: Deps) -> dict:
    """P4.4 — Cascade fallback sul SOSTITUITO (solo se current -> BASSA).

    Le evidence trovate SOVRASCRIVONO quelle povere del current: mai
    contesto misto v1/v2 all'LLM. Fail-closed come retrieval_node."""
    try:
        ev, children = _retrieve_with_terms(deps.retrieve_superseded, state)
        # L'avviso "SOSTITUITA" ha senso SOLO se il fallback ha trovato evidenza:
        # su risultato vuoto si scende comunque a rifiuto, senza avvisare di nulla.
        return {"evidence": ev, "children_evidence": children, "obsolete_warning": bool(ev), "fallback_superseded_done": True,
                "audit": _audit(state, f"fallback su SOSTITUITO: {len(ev)} hit, {len(children)} children")}
    except Exception as exc:
        return {"system_error": True, "level": "BASSA", "fallback_superseded_done": True,
                "audit": _audit(state, f"Sistema non disponibile (fallback_superseded): {exc}")}


def fallback_deleted_node(state: GraphState, deps: Deps) -> dict:
    """P7 — Cascade fallback sull'ELIMINATO (solo se superseded -> BASSA)."""
    try:
        ev, children = _retrieve_with_terms(deps.retrieve_deleted, state)
        # L'avviso "ELIMINATO" ha senso SOLO se il fallback ha trovato evidenza:
        # su risultato vuoto la risposta è un rifiuto puro, senza avviso spurio.
        return {"evidence": ev, "children_evidence": children, "deleted_warning": bool(ev), "fallback_deleted_done": True,
                "audit": _audit(state, f"fallback su ELIMINATO: {len(ev)} hit, {len(children)} children")}
    except Exception as exc:
        return {"system_error": True, "level": "BASSA", "fallback_deleted_done": True,
                "audit": _audit(state, f"Sistema non disponibile (fallback_deleted): {exc}")}


def refuse_node(state: GraphState, deps: Deps) -> dict:
    return {"answer": REFUSAL, "audit": _audit(state, "livello BASSA -> rifiuto")}


def reconcile(state: GraphState) -> dict:
    """Riconciliazione finale pura: accesso / system_error / rifiuto / guardrail."""
    if state.get("access_error"):
        return {"final_answer": ACCESS_MSG, "final_level": "BASSA"}
    if state.get("system_error"):
        return {"final_answer": SYS_UNAVAIL, "final_level": "BASSA"}
    # P7 — documento ELIMINATO: l'avviso deve comparire SEMPRE, anche quando la
    # risposta di base è un rifiuto (level BASSA) o un blocco guardrail. Va quindi
    # gestito PRIMA del return anticipato su BASSA. Cap sempre a BASSA.
    if state.get("deleted_warning"):
        base = state.get("answer") or ""
        if state.get("level") == "BASSA" or not base.strip() or REFUSAL in base:
            base = REFUSAL
        elif state.get("guardrail_gate", "PASS") == "BLOCK":
            base = BLOCK_MSG
        base += (
            "\nATTENZIONE: risposta basata su un documento ELIMINATO dall'archivio. "
            "Questo documento non è più presente nella cartella monitorata. "
            "Le informazioni potrebbero essere obsolete o non più valide. "
            "Verificare con il fornitore o il responsabile tecnico."
        )
        return {"final_answer": base, "final_level": "BASSA"}
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
    # P4.4 — Gestione warning per documento SOSTITUITO (obsoleto).
    # Il ramo ELIMINATO è gestito prima (return anticipato con cap BASSA).
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

def _after_router(state: GraphState, has_sql: bool = False,
                  has_visual: bool = False) -> str:
    if state.get("access_error"):
        return "finalize"
    # Fase 1.3 — biforcazione: STRUCTURED/VISUAL solo se il ramo e' cablato,
    # altrimenti l'intent degrada al RAG testuale esistente (NARRATIVE).
    intent = state.get("intent")
    if has_sql and intent == "structured":
        return "sql"
    if has_visual and intent == "visual":
        return "visual"
    return "expansion"


def _after_visual(state: GraphState) -> str:
    if state.get("system_error"):
        return "finalize"
    if state.get("level") == "BASSA":   # 0 pagine / RBAC filtra tutto -> rifiuto
        return "finalize"
    return "guardrail"


def _after_sql(state: GraphState) -> str:
    if state.get("system_error"):
        return "finalize"
    if state.get("level") == "BASSA":   # 0 righe / codice degenere -> rifiuto
        return "finalize"
    return "render"


def _after_render(state: GraphState) -> str:
    if state.get("system_error"):
        return "finalize"
    if state.get("level") == "BASSA":   # prezzo non ancorato -> rifiuto
        return "finalize"
    return "guardrail"


def _after_retrieval(state: GraphState) -> str:
    return "finalize" if state.get("system_error") else "confidence"


def _after_confidence(state: GraphState, has_fallback_sup: bool = False, has_fallback_del: bool = False) -> str:
    if state.get("level") != "BASSA":
        return "fusion"
    # P4.4/P7 — cascade a tre livelli
    if has_fallback_sup and not state.get("fallback_superseded_done"):
        return "fallback_superseded"
    if has_fallback_del and not state.get("fallback_deleted_done"):
        return "fallback_deleted"
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
    g.add_node("expansion", n(expansion_node))
    g.add_node("sql", n(sql_node))
    g.add_node("render", n(render_node))
    g.add_node("visual", n(visual_node))
    g.add_node("retrieval", n(retrieval_node))
    g.add_node("confidence", n(confidence_node))
    g.add_node("fallback_superseded", n(fallback_superseded_node))
    g.add_node("fallback_deleted", n(fallback_deleted_node))
    g.add_node("fusion", n(fusion_node))
    g.add_node("synthesis", n(synthesis_node))
    g.add_node("guardrail", n(guardrail_node))
    g.add_node("refuse", n(refuse_node))
    g.add_node("finalize", n(finalize_node))

    has_sql = deps.sql_query is not None and deps.llm is not None
    has_visual = deps.visual_search is not None
    g.add_edge(START, "router")
    g.add_conditional_edges("router",
                            lambda s: _after_router(s, has_sql, has_visual),
                            {"finalize": "finalize", "expansion": "expansion",
                             "sql": "sql", "visual": "visual"})
    # Fase 1.3 — ramo STRUCTURED: sql -> render -> guardrail (convergenza invariata)
    g.add_conditional_edges("sql", _after_sql,
                            {"finalize": "finalize", "render": "render"})
    g.add_conditional_edges("render", _after_render,
                            {"finalize": "finalize", "guardrail": "guardrail"})
    # Fase 1.3 — ramo VISUAL: visual -> guardrail (NESSUN LLM; convergenza invariata)
    g.add_conditional_edges("visual", _after_visual,
                            {"finalize": "finalize", "guardrail": "guardrail"})
    g.add_edge("expansion", "retrieval")
    g.add_conditional_edges("retrieval", _after_retrieval,
                            {"finalize": "finalize", "confidence": "confidence"})
    has_fb_sup = deps.retrieve_superseded is not None
    has_fb_del = deps.retrieve_deleted is not None
    g.add_conditional_edges("confidence",
                            lambda s: _after_confidence(s, has_fallback_sup=has_fb_sup, has_fallback_del=has_fb_del),
                            {"refuse": "refuse", "fusion": "fusion",
                             "fallback_superseded": "fallback_superseded",
                             "fallback_deleted": "fallback_deleted"})
    g.add_conditional_edges("fallback_superseded", _after_fallback,
                            {"finalize": "finalize", "confidence": "confidence"})
    g.add_conditional_edges("fallback_deleted", _after_fallback,
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

    from src.backend.clients.qdrant_client import get_qdrant_client
    from src.retrieval.confidence import CALIBRATED_V1
    from src.retrieval.rerank import RERANK_TOPK, _reranker
    from src.retrieval.search import access_filter, search
    from src.retrieval.synthesis import synthesize as _synth

    qc = get_qdrant_client()
    from src.graph.llm import MLXModel, FallbackLLM
    from src.serving.multiplexer import ModelMultiplexer, MlxModelRuntime
    from src.backend.clients.ollama_client import OllamaModel
    from src.backend.config import settings

    mlx_runtime = MlxModelRuntime(default_num_ctx=settings.local_ctx_cap)
    multiplexer = ModelMultiplexer(mlx_runtime, max_resident=1, ctx_cap=settings.local_ctx_cap)
    
    primary_llm = MLXModel(settings.logic_model, multiplexer)
    fallback_llm = OllamaModel()
    
    llm = FallbackLLM(primary_llm, fallback_llm)

    def _to_ev(hit, score) -> Evidence:
        p = hit.payload
        return Evidence(p.get("doc"), p.get("page"), p.get("lang"), p.get("code"),
                        p.get("desc"), p.get("text"), p.get("qty"), p.get("price"),
                        p.get("type"), p.get("status"), p.get("warnings") or [], score,
                        p.get("version_status"), p.get("doc_version"),
                        p.get("is_assembly", False))

    def _apply_hop2(ev_list: list[Evidence]) -> tuple[list[Evidence], list[Evidence]]:
        seen = set()
        grouped = []
        for e in ev_list:
            if e.code not in seen:
                grouped.append(e)
                if e.code:
                    seen.add(e.code)
        
        children = []
        if grouped and grouped[0].is_assembly and grouped[0].code:
            cf = models.Filter(must=[models.FieldCondition(
                key="parent_id", match=models.MatchValue(value=grouped[0].code))])
            records, _ = qc.scroll("aike_chunks", scroll_filter=cf, limit=100)
            for r in records:
                # children inherit score of parent for Context presentation, but are kept separate
                children.append(_to_ev(models.ScoredPoint(
                    id=r.id, version=0, score=grouped[0].score, payload=r.payload, vector=None
                ), grouped[0].score))
        return grouped, children

    def _retrieve(version_status: str):
        # P4.4/P4.5 — stessa logica routed_rr su scope diversi, SEMPRE dentro
        # il perimetro del ruolo: AND(versione, ruolo) — anche il fallback
        # sull'obsoleto resta filtrato per ruolo
        vf = access_filter(version_status, role)

        def retrieve(query: str, extra_terms: list[str] | None = None) -> tuple[list[Evidence], list[Evidence]]:
            route = classify_query(query)
            if route.query_type == "code":
                hits = search(qc, query, "routed", limit=10, query_filter=vf)
                return _apply_hop2([_to_ev(h, h.score) for h in hits])
            # natural/mixed: reranker SIGMOID come score di produzione (P3.2 §3)
            # (l'espansione debito §2 entra solo nel canale sparse via search;
            #  il reranker resta sulla query originale)
            import torch
            base = search(qc, query, "routed", limit=RERANK_TOPK, query_filter=vf,
                          extra_terms=extra_terms)
            if not base:
                return [], []
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
            return _apply_hop2([_to_ev(h, s) for s, h in ranked[:10]])

        return retrieve

    def catalog_lookup(key: str) -> bool:
        r = qc.count("aike_chunks", exact=True, count_filter=models.Filter(
            must=[models.FieldCondition(key="code", match=models.MatchValue(value=key))]))
        return r.count > 0

    def synthesize(query: str, ev: list[Evidence]) -> str:
        return _synth(llm, query, [e._asdict() for e in ev])

    from src.retrieval.expansion import expand_query

    # Fase 1.3 — ramo STRUCTURED: esecutore SQL su connessione read-only (difesa in
    # profondita'). Fail-safe: se il DB e' irraggiungibile o il ruolo manca, sql_query
    # resta None e l'intent STRUCTURED degrada a NARRATIVE (mai un crash all'avvio).
    # NB: il ramo si attiva solo con ANCHE `llm` != None (MLXModel, ancora differito).
    # TODO: [Performance] Implementare pool di connessioni o context manager per
    # evitare leak a ogni answer() (oggi si apre una connessione RO per invocazione).
    try:
        from src.backend.clients.postgres_client import get_pg_conn_readonly
        from src.graph.tools.sql_tool import PgSqlExecutor
        sql_query = PgSqlExecutor(get_pg_conn_readonly())
    except Exception:
        sql_query = None

    # Fase 1.3 — ramo VISUAL: retrieval PixelRAG reale su `aike_pages_visual`
    # (Qwen3-VL + Qdrant), RBAC/versioning fail-closed dentro visual_search.
    # Fail-safe come sopra: se Qdrant/collection/embedder mancano -> None ->
    # VISUAL degrada a NARRATIVE. Lazy: Qwen3VLEmbedder non importa torch
    # finche' non si embedda davvero. Il multiplexer e' opzionale (None in
    # fase 0 single-tenant): se verra' iniettato, l'embedder ne usera'
    # l'hook di eviction; oggi resta compatibile (no-op).
    try:
        from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder
        from src.retrieval.pixelrag.index import QdrantVisualIndex
        from src.graph.tools.visual_tool import make_visual_search
        visual_search = make_visual_search(
            Qwen3VLEmbedder(), QdrantVisualIndex(qc))
    except Exception:
        visual_search = None

    return Deps(retrieve=_retrieve("current"), synthesize=synthesize,
                catalog_lookup=catalog_lookup, thresholds=CALIBRATED_V1,
                retrieve_superseded=_retrieve("superseded"),
                retrieve_deleted=_retrieve("deleted"),
                expand=expand_query, sql_query=sql_query, llm=llm,
                visual_search=visual_search)
