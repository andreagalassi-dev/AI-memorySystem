"""Fase 1.3 Passo 4 — Test del ramo STRUCTURED (sql_node + render + branch).

DB-free e MLX-free: `FakeSqlExecutor` (1.2 Port) + `FakeLLM` (1.3 Port) iniettati in
`Deps`. Copre i nodi in isolamento, l'e2e `answer()` sui tre esiti, le DUE barriere
anti-allucinazione (codice via guardrail P3.1 riusato, prezzo via value-grounding),
il fail-closed su 0 righe, e la ZERO-regressione (sql non cablato -> ramo NARRATIVE).
"""

from __future__ import annotations

from decimal import Decimal

from src.graph.llm import FakeLLM
from src.graph.pipeline import (
    BLOCK_MSG,
    Deps,
    answer,
    render_node,
    sql_node,
    _after_sql,
)
from src.graph.tools.sql_tool import FakeSqlExecutor, LookupRow
from src.retrieval.confidence import Thresholds
from src.retrieval.synthesis import REFUSAL

CODE = "V09199300001"
_T = Thresholds(tau_hi=0.5, tau_lo=0.2)
_GOOD = "Il codice V09199300001 costa 15,50 € (listino_x, pag. 3)."


def _sql(records=None):
    return FakeSqlExecutor(records if records is not None else {
        CODE: {"prezzo": Decimal("15.50"), "descrizione": "Valvola",
               "doc_stem": "listino_x", "page": 3}})


def _deps(*, llm=None, sql=None, retrieve=None):
    return Deps(
        retrieve=retrieve or (lambda q: ([], [])),
        synthesize=lambda q, ev: llm.generate("", "") if llm else "n/a",
        catalog_lookup=lambda k: False,
        thresholds=_T,
        llm=llm,
        sql_query=sql,
    )


# --------------------------------------------------------------- _after_sql routing

class TestAfterSqlRouting:
    def test_sql_zero_righe_fallback_expansion(self):
        state = {"level": "BASSA"}
        assert _after_sql(state) == "expansion"

    def test_codice_non_valido_fallback_expansion(self):
        state = {"level": "BASSA"}
        assert _after_sql(state) == "expansion"

    def test_system_error_fail_closed_finalize(self):
        state = {"level": "BASSA", "system_error": True}
        assert _after_sql(state) == "finalize"

    def test_righe_trovate_prosegue_render(self):
        state = {"level": "ALTA"}
        assert _after_sql(state) == "render"


# --------------------------------------------------------------- sql_node

class TestSqlNode:
    def test_riga_trovata_evidence_e_alta(self):
        out = sql_node({"query": "prezzo", "code_token": CODE}, _deps(sql=_sql()))
        assert out["level"] == "ALTA"
        ev = out["evidence"][0]
        assert ev.code == CODE and ev.price == 15.50
        assert out["sql_rows"][0].valore == Decimal("15.50")

    def test_zero_righe_bassa_fail_closed(self):
        out = sql_node({"query": "prezzo", "code_token": "V09199300009"}, _deps(sql=_sql()))
        assert out["level"] == "BASSA" and out.get("evidence") in (None, [])

    def test_eccezione_e_system_error(self):
        class Boom:
            def run(self, params):
                raise RuntimeError("DB giù")
        out = sql_node({"query": "prezzo", "code_token": CODE}, _deps(sql=Boom()))
        assert out["system_error"] is True and out["level"] == "BASSA"

    def test_righe_presenti_senza_valore_utile_fallback_bassa(self):
        class NullValoreExecutor:
            def run(self, params):
                return [LookupRow(valore=None, doc_stem="listino_x", page=3)]
        out = sql_node({"query": "prezzo", "code_token": CODE}, _deps(sql=NullValoreExecutor()))
        assert out["level"] == "BASSA"
        assert out["sql_rows"] == []


# --------------------------------------------------------------- render_node

class TestRenderNode:
    def _state(self):
        # stato come lo lascia sql_node su successo
        s = sql_node({"query": "quanto costa", "code_token": CODE}, _deps(sql=_sql()))
        return {"query": "quanto costa", "code_token": CODE, **s}

    def test_render_ok_setta_answer_e_citazioni_deterministiche(self):
        llm = FakeLLM(_GOOD)
        out = render_node(self._state(), _deps(llm=llm, sql=_sql()))
        assert out["answer"] == _GOOD
        assert out["citations"] == [("listino_x", 3)]   # da sql_rows, non dall'LLM

    def test_render_prezzo_allucinato_fail_closed(self):
        # prezzo non a DB: value-grounding fallisce -> BASSA (poi REFUSAL)
        llm = FakeLLM("Il codice V09199300001 costa 999,00 € (listino_x, pag. 3).")
        out = render_node(self._state(), _deps(llm=llm, sql=_sql()))
        assert out["level"] == "BASSA"

    def test_render_llm_giu_e_system_error(self):
        llm = FakeLLM(error=RuntimeError("modello giù"))
        out = render_node(self._state(), _deps(llm=llm, sql=_sql()))
        assert out["system_error"] is True and out["level"] == "BASSA"


# --------------------------------------------------------------- e2e answer()

class TestE2EStructured:
    def test_prezzo_ok_alta(self):
        llm = FakeLLM(_GOOD)
        res = answer("prezzo del codice V09199300001", "commerciale",
                     _deps(llm=llm, sql=_sql()))
        assert res["final_level"] == "ALTA"
        assert "15,50" in res["final_answer"]

    def test_bare_code_fast_dispatch(self):
        # codice a secco -> STRUCTURED (default prezzo)
        llm = FakeLLM(_GOOD)
        res = answer("V09199300001", "commerciale", _deps(llm=llm, sql=_sql()))
        assert res["final_level"] == "ALTA" and "15,50" in res["final_answer"]

    def test_codice_allucinato_bloccato_dal_guardrail(self):
        # barriera #1: l'LLM cita un codice ASSENTE dall'evidenza -> BLOCK
        llm = FakeLLM("Il codice V09199300099 costa 15,50 € (listino_x, pag. 3).")
        res = answer("prezzo del codice V09199300001", "commerciale",
                     _deps(llm=llm, sql=_sql()))
        assert res["final_answer"] == BLOCK_MSG and res["final_level"] == "BASSA"

    def test_prezzo_allucinato_rifiutato(self):
        # barriera #2: prezzo non a DB -> REFUSAL
        llm = FakeLLM("Il codice V09199300001 costa 999,00 € (listino_x, pag. 3).")
        res = answer("prezzo del codice V09199300001", "commerciale",
                     _deps(llm=llm, sql=_sql()))
        assert res["final_answer"] == REFUSAL and res["final_level"] == "BASSA"

    def test_codice_assente_refusal_e_llm_non_chiamato(self):
        llm = FakeLLM(_GOOD)
        res = answer("prezzo del codice V09199300009", "commerciale",
                     _deps(llm=llm, sql=_sql()))
        assert res["final_answer"] == REFUSAL and res["final_level"] == "BASSA"
        assert llm.calls == []   # su 0 righe non si spreca l'LLM

    def test_ruolo_invalido_solleva(self):
        # answer() valida il ruolo a monte (fail-closed): mai instradare senza ruolo
        import pytest
        llm = FakeLLM(_GOOD)
        with pytest.raises(ValueError, match="ruolo ignoto"):
            answer("prezzo del codice V09199300001", "ospite", _deps(llm=llm, sql=_sql()))

    def test_sql_vuoto_fallback_su_retrieval(self):
        llm = FakeLLM("Trovato da Qdrant (doc, pag. 1).")
        called = {"retrieve": False}

        def retrieve(q, *a):
            called["retrieve"] = True
            from src.graph.pipeline import Evidence
            return ([Evidence(doc="doc", page=1, lang="it", code="V09199300009", desc=None, text="testo", qty=None, price=None, type="text", status="clean", warnings=[], score=0.9)], [])

        res = answer("prezzo del codice V09199300009", "commerciale",
                     _deps(llm=llm, sql=_sql(), retrieve=retrieve))
        
        assert called["retrieve"] is True
        assert res["final_level"] in ["MEDIA", "ALTA"]
        assert "Trovato da Qdrant" in res["final_answer"]


# --------------------------------------------------------------- zero-regressione

class TestFallbackNarrative:
    def test_structured_senza_sql_cablato_va_a_narrative(self):
        # sql_query=None: il ramo STRUCTURED non è cablato -> retrieval (NARRATIVE)
        called = {"retrieve": False}

        def retrieve(q, *a):
            called["retrieve"] = True
            return ([], [])

        answer("prezzo del codice V09199300001", "commerciale",
               _deps(llm=None, sql=None, retrieve=retrieve))
        assert called["retrieve"] is True
