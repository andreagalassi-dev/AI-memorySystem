"""P4.4 — Test del Cascade Fallback (current -> superseded) e avviso obsoleto.

Vincoli sotto test: fallback SOLO quando la via current porterebbe a BASSA;
al massimo UN ciclo (fallback_done); le evidence superseded SOVRASCRIVONO
quelle current (mai contesto misto v1/v2 all'LLM); l'avviso obsoleto arriva
a finalize e cappa il livello a MEDIA; con retrieve_superseded=None il
grafo si comporta esattamente come in P3.5.
"""

from __future__ import annotations

from src.graph.pipeline import (
    Deps,
    Evidence,
    build_graph,
    fallback_node,
    reconcile,
)
from src.retrieval.confidence import Thresholds
from src.retrieval.synthesis import REFUSAL

T = Thresholds(tau_hi=0.5, tau_lo=0.2)

EV_CUR_POOR = Evidence(doc="mamoli_v2", page=3, lang="IT", code=None,
                       desc="pagina indice", text="indice generale", qty=None,
                       price=None, type="text", status="ok", warnings=[],
                       score=0.05, version_status="current", doc_version=2)
EV_OLD = Evidence(doc="mamoli_v1", page=41, lang="IT", code="MX-100",
                  desc="GUARNIZIONE VALVOLA", text=None, qty=1, price=None,
                  type="table_row", status="ok", warnings=[], score=9.0,
                  version_status="superseded", doc_version=1)
EV_OLD2 = EV_OLD._replace(code="MX-101", score=2.0)


def make_deps(retrieve=None, retrieve_superseded=None, synthesize=None,
              catalog=None):
    return Deps(
        retrieve=retrieve or (lambda q: []),
        synthesize=synthesize or (
            lambda q, ev: "La guarnizione è MX-100 (mamoli_v1, pag. 41)."),
        catalog_lookup=catalog or (lambda k: False),
        thresholds=T,
        retrieve_superseded=retrieve_superseded,
    )


# ------------------------------------------------------------ Evidence additiva

class TestEvidenceRetrocompat:
    def test_costruzione_posizionale_a_12_campi(self):
        ev = Evidence("doc", 1, "IT", None, None, None, None, None,
                      "text", "ok", [], 1.0)
        assert ev.version_status is None and ev.doc_version is None


# ------------------------------------------------------------ nodo fallback

class TestFallbackNode:
    def test_sovrascrive_evidence_e_accende_flag(self):
        st = {"query": "guarnizione", "evidence": [EV_CUR_POOR]}
        out = fallback_node(st, make_deps(
            retrieve_superseded=lambda q: [EV_OLD, EV_OLD2]))
        assert out["evidence"] == [EV_OLD, EV_OLD2]   # SOVRASCRITTE, non appese
        assert out["obsolete_warning"] is True
        assert out["fallback_done"] is True

    def test_eccezione_fail_closed(self):
        def boom(q):
            raise ConnectionError("qdrant down")
        out = fallback_node({"query": "x"}, make_deps(retrieve_superseded=boom))
        assert out["system_error"] and out["fallback_done"]


# ------------------------------------------------------------ reconcile puro

class TestReconcileObsoleto:
    def test_avviso_e_cap_media(self):
        r = reconcile({"level": "ALTA", "guardrail_gate": "PASS",
                       "answer": "MX-100", "obsolete_warning": True})
        assert r["final_level"] == "MEDIA"
        assert "SOSTITUITA" in r["final_answer"] or "obsolet" in r["final_answer"].lower()

    def test_block_prevale_sull_avviso(self):
        r = reconcile({"level": "ALTA", "guardrail_gate": "BLOCK",
                       "answer": "codice X", "obsolete_warning": True})
        assert r["final_level"] == "BASSA"
        assert "verifica manuale" in r["final_answer"].lower()

    def test_senza_avviso_nessun_cambiamento(self):
        r = reconcile({"level": "ALTA", "guardrail_gate": "PASS", "answer": "X"})
        assert r["final_level"] == "ALTA" and r["final_answer"] == "X"


# ------------------------------------------------------------ end-to-end

class TestCascadeEndToEnd:
    def _run(self, deps, query="che codice ha la guarnizione valvola?"):
        # P4.5: il grafo esige un ruolo valido nello stato (fail-closed)
        return build_graph(deps).invoke({"query": query, "role": "tecnico"})

    def test_current_vuoto_fallback_risponde_con_avviso(self):
        r = self._run(make_deps(retrieve_superseded=lambda q: [EV_OLD, EV_OLD2],
                                catalog=lambda k: True))
        assert r["final_level"] == "MEDIA"            # cap: mai ALTA da obsoleto
        assert "MX-100" in r["final_answer"]
        assert "obsolet" in r["final_answer"].lower() or "SOSTITUITA" in r["final_answer"]
        assert any("fallback" in a.lower() for a in r["audit"])

    def test_current_povero_niente_frankenstein(self):
        # current NON vuoto ma sotto soglia (margine (s1-s2)/s1 = 0.02 <
        # tau_lo: servono DUE hit, con uno solo il margine e' forzato a 1.0
        # per design P3.2) -> BASSA -> fallback: all'LLM arrivano SOLO le
        # evidence superseded, mai il mix v1/v2
        seen = []
        def synth(q, ev):
            seen.append(list(ev))
            return "La guarnizione è MX-100 (mamoli_v1, pag. 41)."
        cur2 = EV_CUR_POOR._replace(page=4, score=0.049)
        self._run(make_deps(retrieve=lambda q: [EV_CUR_POOR, cur2],
                            retrieve_superseded=lambda q: [EV_OLD, EV_OLD2],
                            synthesize=synth, catalog=lambda k: True))
        assert seen == [[EV_OLD, EV_OLD2]]

    def test_entrambi_vuoti_rifiuto_senza_loop(self):
        calls = []
        def sup(q):
            calls.append(1)
            return []
        r = self._run(make_deps(retrieve_superseded=sup))
        assert r["final_level"] == "BASSA" and r["final_answer"] == REFUSAL
        assert len(calls) == 1                        # UNA sola cascata, no loop

    def test_current_buono_fallback_mai_chiamato(self):
        called = []
        def sup(q):
            called.append(1)
            return [EV_OLD]
        ev_good = EV_OLD._replace(version_status="current", doc_version=2,
                                  doc="mamoli_v2")
        r = self._run(make_deps(retrieve=lambda q: [ev_good, EV_OLD2],
                                retrieve_superseded=sup, catalog=lambda k: True))
        assert not called                             # zero costo quando non serve
        assert "obsolet" not in r["final_answer"].lower()

    def test_dep_assente_comportamento_p35_invariato(self):
        r = self._run(make_deps())                    # retrieve_superseded=None
        assert r["final_level"] == "BASSA" and r["final_answer"] == REFUSAL
