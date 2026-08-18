"""P3.5 — Test del grafo LangGraph (FSM deterministica).

Nodi puri dove possibile, client iniettato (fake). Copre: riconciliazione
finale, nodi in isolamento, FAIL-CLOSED di rete (Qdrant/Ollama giu' -> BASSA),
ed end-to-end con dipendenze fittizie (incluso 'synthesize NON chiamato su BASSA').
"""

from __future__ import annotations

from src.retrieval.confidence import Thresholds
from src.retrieval.synthesis import REFUSAL, extract_citations
from src.graph.pipeline import (
    Deps,
    Evidence,
    build_graph,
    confidence_node,
    guardrail_node,
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
        retrieve=retrieve or (lambda q: ([EV_CODE, EV2], [])),
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

    # --- epica D p.2: la guardia "evidenza singola" deve valere NEL NODO ---

    def test_confidence_singleton_narrativo_non_e_alta(self):
        # un solo gruppo-codice su rotta NATURALE e' la situazione MENO
        # corroborata possibile: compute_margin da' 1.0 per convenzione
        # (edge case "0 hit -> 0.0, 1 hit -> 1.0"), non perche' ci sia
        # separazione misurata -> mai ALTA. Caso reale: NARR-Q035.
        st = {"query": "aeratore 24x1 di ricambio", "route": "natural",
              "evidence": [EV_CODE]}
        out = confidence_node(st, make_deps())
        assert out["margin"] == 1.0
        assert out["level"] == "MEDIA"

    def test_confidence_singleton_senza_codice_narrativo_non_e_alta(self):
        # il caso tipico del narrativo: un unico chunk di testo, nessun codice
        st = {"query": "come si smonta il gruppo", "route": "natural",
              "evidence": [EV_CODE._replace(code=None, type="text")]}
        out = confidence_node(st, make_deps())
        assert out["margin"] == 1.0 and out["level"] == "MEDIA"

    def test_confidence_rotta_code_resta_esente_da_guardia_singleton(self):
        # ESENZIONE VOLUTA: sulla rotta 'code' il guardrail deterministico
        # valida gia' il codice -> un solo gruppo NON degrada da ALTA.
        st = {"query": "codice 877615", "route": "code", "evidence": [EV_CODE]}
        out = confidence_node(st, make_deps())
        assert out["margin"] == 1.0 and out["level"] == "ALTA"

    def test_confidence_rotta_code_duplicati_stesso_codice_resta_alta(self):
        # stesso caso del debito §3 (0USBX4): 2 hit -> 1 gruppo -> singleton,
        # ma rotta code -> ALTA preservata (ramo saturo 22/22 PASS_ALTA).
        st = {"query": "codice 877615", "route": "code",
              "evidence": [EV_CODE, EV_CODE._replace(desc="PISTON PIN (DE)")]}
        out = confidence_node(st, make_deps())
        assert out["level"] == "ALTA"

    def test_confidence_due_gruppi_narrativo_resta_alta(self):
        # non-regressione: la guardia colpisce SOLO il singleton, non il
        # narrativo corroborato da piu' gruppi-codice distinti.
        st = {"query": "spinotto", "route": "natural",
              "evidence": [EV_CODE, EV2._replace(score=0.5)]}
        out = confidence_node(st, make_deps())
        assert out["margin"] >= T.tau_hi and out["level"] == "ALTA"

    def test_confidence_due_chunk_senza_codice_non_sono_singleton(self):
        # i chunk senza codice ricevono una chiave unica ("_nocode", i):
        # NON collassano in un gruppo solo -> la guardia non scatta.
        a = EV_CODE._replace(code=None, type="text")
        b = EV_CODE._replace(code=None, type="text", desc="ALTRO", score=0.5)
        st = {"query": "smontaggio", "route": "natural", "evidence": [a, b]}
        out = confidence_node(st, make_deps())
        assert out["level"] == "ALTA"


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
        r = self._run(make_deps(retrieve=lambda q: ([], []), synthesize=synth))
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


# ------------------------------------------- wiring citazioni -> guardrail (C005)

# Forma del caso golden C005: la descrizione della riga recuperata contiene
# token code-shaped che NON sono part-number. Il guardrail deve poterli
# confermare, ma solo se la pagina della riga e' quella citata: serve quindi
# che guardrail_node passi le citazioni a check_answer.
EV_C005 = Evidence(
    doc="rotax_912_914_IPC", page=79, lang="EN", code="961267",
    desc="CABLE SUPPORT ASSY. For cylinder 2 and 4 SI-912-012 SI-914-014",
    text=None, qty=1, price=None, type="table_row", status="clean",
    warnings=[], score=18.4)

C005_ANSWER = ("Il codice 961267 è un CABLE SUPPORT ASSY. per i cilindri 2 e 4 "
               "SI-912-012 SI-914-014 (rotax_912_914_IPC, pag. 79).")


class TestGuardrailCitazioni:
    def test_c005_non_bloccato_quando_la_pagina_e_citata(self):
        st = {"query": "codice 961267", "evidence": [EV_C005],
              "answer": C005_ANSWER,
              "citations": extract_citations(C005_ANSWER)}
        out = guardrail_node(st, make_deps())
        assert out["guardrail_gate"] == "PASS", out["audit"]

    def test_c005_verify_se_la_pagina_citata_e_un_altra(self):
        st = {"query": "codice 961267", "evidence": [EV_C005],
              "answer": C005_ANSWER, "citations": [("rotax_912_914_IPC", 12)]}
        out = guardrail_node(st, make_deps())
        assert out["guardrail_gate"] == "VERIFY", out["audit"]

    def test_c005_e2e_livello_non_bassa(self):
        deps = make_deps(retrieve=lambda q: ([EV_C005], []),
                         synthesize=lambda q, ev: C005_ANSWER)
        r = build_graph(deps).invoke({"query": "codice 961267", "role": "tecnico"})
        assert r["final_level"] != "BASSA", r["final_answer"]
        assert "961267" in r["final_answer"] and "pag. 79" in r["final_answer"]
