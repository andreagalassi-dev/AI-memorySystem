"""Fase 1.3 Passo 6 — Contratto e2e del Cervello Unificatore (tri-intent).

Un UNICO `Deps` con TUTTI i rami cablati (FakeLLM + FakeSqlExecutor per STRUCTURED,
visual fake per VISUAL, retrieve+synthesize per NARRATIVE). Fotografa il contratto
definitivo dell'orchestratore: la stessa `answer(query, role)` instrada in modo
deterministico sui tre intent, ognuno col proprio livello di confidence, e TUTTI
convergono su guardrail->finalize. Include la difesa in profondita' (il guardrail
codici agisce anche sulle risposte SQL renderizzate) e il fail-closed condiviso.

GPU-free, DB-free, MLX-free: nessun client reale.
"""

from __future__ import annotations

from decimal import Decimal

from src.graph.llm import FakeLLM
from src.graph.pipeline import BLOCK_MSG, Deps, Evidence, answer
from src.graph.tools.sql_tool import FakeSqlExecutor
from src.retrieval.confidence import Thresholds
from src.retrieval.pixelrag.index import PageHit
from src.retrieval.synthesis import REFUSAL

_T = Thresholds(tau_hi=0.5, tau_lo=0.2)
CODE = "V09199300001"

# --- evidenza NARRATIVE (codici presenti a catalogo -> guardrail PASS) -------
_EV = [Evidence(doc="manuale_x", page=5, lang="IT", code="877615", desc="VALVOLA",
                text=None, qty=1, price=None, type="table_row", status="clean",
                warnings=[], score=9.0),
       Evidence(doc="manuale_x", page=5, lang="IT", code="877616", desc="MOLLA",
                text=None, qty=1, price=None, type="table_row", status="clean",
                warnings=[], score=2.0)]
_NARR_ANSWER = "La valvola si smonta dal collettore (manuale_x, pag. 5)."
_PRICE_ANSWER = f"Il codice {CODE} costa 15,50 € (listino_x, pag. 3)."
_VISUAL_PAGES = [PageHit(doc="esploso_pompa", page=7, score=0.9, n_tiles=2)]


def _full_deps(*, llm_answer=_PRICE_ANSWER, narr_spy=None):
    """Deps completo: tutti e tre i rami cablati dietro i Fake."""
    def retrieve(q, *a):
        if narr_spy is not None:
            narr_spy.append(q)
        return (list(_EV), [])

    return Deps(
        retrieve=retrieve,
        synthesize=lambda q, ev: _NARR_ANSWER,
        catalog_lookup=lambda k: k in {"877615", "877616"},
        thresholds=_T,
        llm=FakeLLM(llm_answer),
        sql_query=FakeSqlExecutor({CODE: {
            "prezzo": Decimal("15.50"), "descrizione": "Valvola",
            "doc_stem": "listino_x", "page": 3}}),
        visual_search=lambda q, role: list(_VISUAL_PAGES),
    )


# ---------------------------------------------------- instradamento tri-intent

class TestTriIntentRouting:
    def test_structured_lookup_alta(self):
        res = answer(f"prezzo del codice {CODE}", "commerciale", _full_deps())
        assert res["final_level"] == "ALTA"
        assert "15,50" in res["final_answer"]

    def test_visual_puntatore_media(self):
        res = answer("mostrami l'esploso del gruppo pompa", "tecnico", _full_deps())
        assert res["final_level"] == "MEDIA"
        assert "(esploso_pompa, pag. 7)" in res["final_answer"]

    def test_narrative_rag_testuale(self):
        spy = []
        res = answer("come si smonta la valvola del collettore", "tecnico",
                     _full_deps(narr_spy=spy))
        assert res["final_level"] in ("ALTA", "MEDIA")
        assert res["final_answer"].startswith(_NARR_ANSWER)  # RAG testuale ha risposto
        assert spy == ["come si smonta la valvola del collettore"]  # retrieve invocato


# ---------------------------------------- difesa in profondita' condivisa

class TestConvergenzaGuardrail:
    def test_guardrail_blocca_codice_allucinato_anche_su_ramo_sql(self):
        # STRUCTURED: prezzo CORRETTO (supera Barriera #2) ma codice ASSENTE
        # dall'evidenza SQL -> il guardrail P3.1 (Barriera #1) blocca comunque.
        # (la difesa in profondita' vale su TUTTI i rami, non solo NARRATIVE)
        deps = _full_deps(
            llm_answer="Il codice V09199300099 costa 15,50 € (listino_x, pag. 3).")
        res = answer(f"prezzo del codice {CODE}", "commerciale", deps)
        assert res["final_answer"] == BLOCK_MSG and res["final_level"] == "BASSA"


# ---------------------------------------- fail-closed condiviso (tutti i rami)

class TestFailClosedCondiviso:
    def test_ruolo_invalido_solleva_a_monte(self):
        import pytest
        with pytest.raises(ValueError, match="ruolo ignoto"):
            answer("mostrami l'esploso", "ospite", _full_deps())

    def test_visual_zero_pagine_refusal(self):
        deps = _full_deps()
        deps.visual_search = lambda q, role: []
        res = answer("mostrami l'esploso del gruppo pompa", "tecnico", deps)
        assert res["final_answer"] == REFUSAL and res["final_level"] == "BASSA"

    def test_structured_zero_righe_refusal(self):
        res = answer("prezzo del codice V09199300009", "commerciale", _full_deps())
        assert res["final_answer"] == REFUSAL and res["final_level"] == "BASSA"
