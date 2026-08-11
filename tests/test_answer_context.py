"""Epica F.1a — `answer()` con contesto conversazionale (DB-free, no rete, LLM FINTO).

Verifica che, con un contesto non vuoto, la query RISOLTA (non l'ellittica
originale) guidi il grafo tri-intent; e che con contesto assente/vuoto il
comportamento sia INVARIATO rispetto ad oggi. Riusa i Fake di
`test_graph_triintent`.
"""

from __future__ import annotations

from decimal import Decimal

from src.graph.context import ContextAssembler, ConversationContext
from src.graph.llm import FakeLLM
from src.graph.pipeline import Deps, Evidence, answer
from src.graph.tools.sql_tool import FakeSqlExecutor
from src.retrieval.confidence import Thresholds

_T = Thresholds(tau_hi=0.5, tau_lo=0.2)
CODE = "V09199300001"

_EV = [Evidence(doc="manuale_x", page=5, lang="IT", code="877615", desc="VALVOLA",
                text=None, qty=1, price=None, type="table_row", status="clean",
                warnings=[], score=9.0)]
_NARR_ANSWER = "La valvola si smonta dal collettore (manuale_x, pag. 5)."


def _deps(*, narr_spy=None, rewrite_out=None):
    """Deps con LLM handler che distingue il prompt di RIFORMULAZIONE dal resto.

    Se `rewrite_out` e' dato, la riformulazione lo restituisce; altrimenti eco
    della domanda. Ogni altra chiamata LLM (render) restituisce una risposta di
    prezzo generica (non usata nei rami narrative)."""
    def handler(system: str, user: str) -> str:
        if "RIFORMULATORE" in system:
            return rewrite_out if rewrite_out is not None else user
        return f"Il codice {CODE} costa 15,50 € (listino_x, pag. 3)."

    def retrieve(q, *a):
        if narr_spy is not None:
            narr_spy.append(q)
        return (list(_EV), [])

    return Deps(
        retrieve=retrieve,
        synthesize=lambda q, ev: _NARR_ANSWER,
        catalog_lookup=lambda k: k in {"877615"},
        thresholds=_T,
        llm=FakeLLM(handler=handler),
        sql_query=FakeSqlExecutor({CODE: {
            "prezzo": Decimal("15.50"), "descrizione": "Valvola",
            "doc_stem": "listino_x", "page": 3}}),
        visual_search=lambda q, role: [],
    )


def _ctx(*pairs):
    return ContextAssembler().build(
        [{"role": r, "content": c} for r, c in pairs])


# ------------------------------------------------- contesto guida la query risolta

def test_context_drives_resolved_query_into_graph():
    spy = []
    ctx = _ctx(("user", "parlami della valvola del collettore"),
               ("assistant", "La valvola del collettore e' a pagina 5."))
    res = answer("come si smonta?", "tecnico",
                 _deps(narr_spy=spy,
                       rewrite_out="come si smonta la valvola del collettore"),
                 context=ctx)
    # Il grafo ha ricevuto la query RISOLTA, non l'ellittica originale.
    assert spy == ["come si smonta la valvola del collettore"]
    assert res["final_answer"].startswith(_NARR_ANSWER)


def test_context_none_behaviour_unchanged():
    spy = []
    res = answer("come si smonta la valvola del collettore", "tecnico",
                 _deps(narr_spy=spy), context=None)
    assert spy == ["come si smonta la valvola del collettore"]
    assert res["final_answer"].startswith(_NARR_ANSWER)


def test_context_empty_behaviour_unchanged_no_llm_rewrite():
    spy = []
    res = answer("come si smonta la valvola del collettore", "tecnico",
                 _deps(narr_spy=spy), context=ConversationContext())
    # Contesto vuoto -> nessuna riformulazione -> query originale nel grafo.
    assert spy == ["come si smonta la valvola del collettore"]
    assert res["final_answer"].startswith(_NARR_ANSWER)


def test_invented_id_in_rewrite_falls_back_to_original():
    # La riformulazione inventa un codice assente dal contesto: la guardia M1 lo
    # scarta e il grafo gira sulla domanda ORIGINALE.
    spy = []
    ctx = _ctx(("user", "parlami della valvola"))
    res = answer("come si smonta?", "tecnico",
                 _deps(narr_spy=spy, rewrite_out="come si smonta il codice ZZ999"),
                 context=ctx)
    assert spy == ["come si smonta?"]  # originale, rewrite scartato
    assert res["final_answer"].startswith(_NARR_ANSWER)


def test_audit_records_contextualization():
    ctx = _ctx(("user", "parlami della valvola del collettore"))
    res = answer("come si smonta?", "tecnico",
                 _deps(rewrite_out="come si smonta la valvola del collettore"),
                 context=ctx)
    audit_blob = "\n".join(res.get("audit", []))
    assert "contextualize" in audit_blob
    assert "come si smonta la valvola del collettore" in audit_blob
