"""P3.5 — Test del grafo LangGraph (FSM deterministica).

Nodi puri dove possibile, client iniettato (fake). Copre: riconciliazione
finale, nodi in isolamento, FAIL-CLOSED di rete (Qdrant/Ollama giu' -> BASSA),
ed end-to-end con dipendenze fittizie (incluso 'synthesize NON chiamato su BASSA').
"""

from __future__ import annotations

from src.retrieval.confidence import Thresholds
from src.retrieval.synthesis import REFUSAL
from src.graph.pipeline import (
    Deps,
    Evidence,
    build_graph,
    confidence_node,
    reconcile,
    retrieval_node,
    synthesis_node,
)

EV_CODE = Evidence(doc="rotax_912_914_IPC", page=22, lang="EN", code="877615",
                   desc="PISTON PIN", text=None, qty=1, price=None,
                   type="table_row", status="clean", warnings=[], score=9.0)
EV2 = EV_CODE._replace(code="877616", desc="RING", score=2.0)

T = Thresholds(tau_hi=0.5, tau_lo=0.2)


def make_deps(retrieve=None, synthesize=None, catalog=None, cross_probe=None):
    return Deps(
        retrieve=retrieve or (lambda q: [EV_CODE, EV2]),
        synthesize=synthesize or (lambda q, ev: "Lo spinotto è 877615 (rotax_912_914_IPC, pag. 22)."),
        catalog_lookup=catalog or (lambda k: False),
        thresholds=T,
        cross_probe=cross_probe,
    )


# --------------------------------------------------------------- reconcile puro

class TestReconcile:
    def test_system_error(self):
        r = reconcile({"system_error": True})
        assert r["final_level"] == "BASSA" and "non disponibile" in r["final_answer"].lower()

    def test_rifiuto_bassa(self):
        r = reconcile({"level": "BASSA"})
        assert r["final_level"] == "BASSA" and r["final_answer"] == REFUSAL

    def test_guardrail_block_degrada(self):
        r = reconcile({"level": "ALTA", "guardrail_gate": "BLOCK", "answer": "codice X"})
        assert r["final_level"] == "BASSA" and "verifica manuale" in r["final_answer"].lower()

    def test_guardrail_verify_capa_media(self):
        r = reconcile({"level": "ALTA", "guardrail_gate": "VERIFY", "answer": "X"})
        assert r["final_level"] == "MEDIA"

    def test_pass_mantiene_livello(self):
        r = reconcile({"level": "ALTA", "guardrail_gate": "PASS", "answer": "X"})
        assert r["final_level"] == "ALTA" and r["final_answer"] == "X"

    def test_rifiuto_llm_forza_bassa(self):
        # l'LLM rifiuta pur con retrieval a MEDIA -> il livello finale e' BASSA
        r = reconcile({"level": "MEDIA", "guardrail_gate": "PASS", "answer": REFUSAL})
        assert r["final_level"] == "BASSA" and r["final_answer"] == REFUSAL


# --------------------------------------------------------------- nodi isolati

class TestNodi:
    def test_confidence_da_evidenza(self):
        st = {"query": "spinotto", "route": "natural", "evidence": [EV_CODE, EV2]}
        out = confidence_node(st, make_deps())
        assert out["has_evidence"] and out["level"] in ("ALTA", "MEDIA", "BASSA")

    def test_confidence_senza_evidenza_bassa(self):
        st = {"query": "x", "route": "natural", "evidence": []}
        assert confidence_node(st, make_deps())["level"] == "BASSA"

    def test_confidence_duplicati_stesso_codice_non_ambigui(self):
        # debito §3 (caso 0USBX4): stesso codice su piu' righe a score identico
        # -> 1 gruppo -> margine 1.0, niente rifiuto per falsa ambiguita'
        dup = EV_CODE._replace(desc="PISTON PIN (DE)")
        st = {"query": "codice 877615", "route": "code",
              "evidence": [EV_CODE, dup]}
        out = confidence_node(st, make_deps())
        assert out["margin"] == 1.0 and out["level"] != "BASSA"
        assert "1 gruppi-codice da 2 hit" in out["audit"][-1]

    def test_confidence_codice_riparato_mai_alta(self):
        # debito §4: warning fuzzy_code sul top hit -> unreliable -> cap MEDIA
        # anche con margine perfetto
        ev = EV_CODE._replace(warnings=["fuzzy_code"])
        st = {"query": "codice 877615", "route": "code", "evidence": [ev]}
        out = confidence_node(st, make_deps())
        assert out["unreliable"] and out["margin"] == 1.0
        assert out["level"] == "MEDIA"

    def test_confidence_codici_diversi_pari_score_restano_ambigui(self):
        # ambiguita' VERA: due codici DIVERSI a pari punteggio -> margine 0
        st = {"query": "anello", "route": "natural",
              "evidence": [EV_CODE, EV2._replace(score=9.0)]}
        out = confidence_node(st, make_deps())
        assert out["margin"] == 0.0 and out["level"] == "BASSA"


# --------------------------------------------------------------- fail-closed rete

class TestFailClosed:
    def test_retrieval_eccezione_forza_bassa(self):
        def boom(q):
            raise ConnectionError("qdrant down")
        out = retrieval_node({"query": "x"}, make_deps(retrieve=boom))
        assert out["system_error"] and any("non disponibile" in a.lower() for a in out["audit"])

    def test_synthesis_eccezione_forza_bassa(self):
        def boom(q, ev):
            raise TimeoutError("ollama down")
        st = {"query": "x", "evidence": [EV_CODE]}
        out = synthesis_node(st, make_deps(synthesize=boom))
        assert out["system_error"] and any("non disponibile" in a.lower() for a in out["audit"])


# --------------------------------------------------------------- end-to-end

class TestEndToEnd:
    def _run(self, deps, query="che codice ha lo spinotto?"):
        # P4.5: il grafo esige un ruolo valido nello stato (fail-closed)
        return build_graph(deps).invoke({"query": query, "role": "tecnico"})

    def test_percorso_normale_risponde_con_fonte(self):
        r = self._run(make_deps())
        assert r["final_level"] in ("ALTA", "MEDIA")
        assert "877615" in r["final_answer"] and "pag. 22" in r["final_answer"]

    def test_bassa_rifiuta_senza_chiamare_llm(self):
        called = []
        def synth(q, ev):
            called.append(1)
            return "non dovrei essere chiamato"
        r = self._run(make_deps(retrieve=lambda q: [], synthesize=synth))
        assert r["final_level"] == "BASSA" and not called  # LLM MAI chiamato

    def test_guardrail_block_su_codice_allucinato(self):
        # l'LLM cita un codice NON in evidenza e non a catalogo -> BLOCK -> verifica
        r = self._run(make_deps(
            synthesize=lambda q, ev: "Usa il codice 999999 (rotax_912_914_IPC, pag. 22)."))
        assert r["final_level"] == "BASSA" and "verifica manuale" in r["final_answer"].lower()

    def test_qdrant_down_sistema_non_disponibile(self):
        def boom(q):
            raise ConnectionError("qdrant down")
        r = self._run(make_deps(retrieve=boom))
        assert r["final_level"] == "BASSA" and "non disponibile" in r["final_answer"].lower()

    def test_ollama_down_sistema_non_disponibile(self):
        def boom(q, ev):
            raise TimeoutError("ollama down")
        r = self._run(make_deps(synthesize=boom))
        assert r["final_level"] == "BASSA" and "non disponibile" in r["final_answer"].lower()
