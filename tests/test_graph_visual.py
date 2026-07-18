"""Fase 1.3 Passo 5 — Test del ramo VISUAL nel grafo (visual_node + branch).

GPU-free e DB-free: un `visual_search` fake iniettato in `Deps`. Copre il nodo in
isolamento (pagine -> MEDIA, 0 pagine -> BASSA, eccezione I/O -> system_error), l'e2e
`answer()` su query visiva ("mostrami l'esploso ..."), il RBAC fail-closed che non
fa trapelare tile a un ruolo non autorizzato, e la ZERO-regressione (visual_search
non cablato -> l'intent VISUAL degrada al ramo NARRATIVE esistente).
"""

from __future__ import annotations

from src.common.roles import Role
from src.graph.pipeline import Deps, answer, visual_node
from src.retrieval.confidence import Thresholds
from src.retrieval.pixelrag.index import PageHit
from src.retrieval.synthesis import REFUSAL, extract_citations

_T = Thresholds(tau_hi=0.5, tau_lo=0.2)


def _pages():
    return [PageHit(doc="esploso_pompa", page=7, score=0.9, n_tiles=2),
            PageHit(doc="esploso_pompa", page=8, score=0.7, n_tiles=1)]


def _deps(*, visual=None, retrieve=None):
    return Deps(
        retrieve=retrieve or (lambda q: ([], [])),
        synthesize=lambda q, ev: "n/a",
        catalog_lookup=lambda k: False,
        thresholds=_T,
        visual_search=visual,
    )


# --------------------------------------------------------------- visual_node

class TestVisualNode:
    def test_pagine_trovate_media_con_puntatore_e_citazioni(self):
        deps = _deps(visual=lambda q, role: _pages())
        out = visual_node({"query": "esploso", "role": "tecnico"}, deps)
        assert out["level"] == "MEDIA"
        assert out["visual_pages"] == _pages()
        assert out["citations"] == [("esploso_pompa", 7), ("esploso_pompa", 8)]
        # la risposta e' un puntatore parsabile, senza LLM
        assert extract_citations(out["answer"]) == out["citations"]

    def test_zero_pagine_bassa_fail_closed(self):
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, role: []))
        assert out["level"] == "BASSA" and out.get("answer") in (None,)

    def test_eccezione_io_e_system_error(self):
        def boom(q, role):
            raise RuntimeError("Qdrant giù")
        out = visual_node({"query": "esploso", "role": "tecnico"}, _deps(visual=boom))
        assert out["system_error"] is True and out["level"] == "BASSA"

    def test_ruolo_passato_al_visual_search(self):
        seen = {}

        def spy(q, role):
            seen["role"] = role
            return _pages()
        visual_node({"query": "esploso", "role": "commerciale"}, _deps(visual=spy))
        assert seen["role"] == Role.COMMERCIALE  # ruolo tipizzato, non stringa


# --------------------------------------------------------------- e2e answer()

class TestE2EVisual:
    def test_esploso_media_con_citazione(self):
        res = answer("mostrami l'esploso del gruppo pompa", "tecnico",
                     _deps(visual=lambda q, role: _pages()))
        assert res["final_level"] == "MEDIA"
        assert ("esploso_pompa", 7) in extract_citations(res["final_answer"])

    def test_zero_pagine_refusal(self):
        res = answer("mostrami l'esploso del gruppo pompa", "tecnico",
                     _deps(visual=lambda q, role: []))
        assert res["final_answer"] == REFUSAL and res["final_level"] == "BASSA"

    def test_rbac_fail_closed_nessun_leak(self):
        # il visual_search reale filtra per ruolo: qui simuliamo che il commerciale
        # NON riceva pagine (tile riservati al tecnico) -> REFUSAL, mai un leak
        def by_role(q, role):
            return _pages() if role == Role.TECNICO else []
        vis = _deps(visual=by_role)
        assert answer("mostrami l'esploso", "tecnico", vis)["final_level"] == "MEDIA"
        r = answer("mostrami l'esploso", "commerciale", vis)
        assert r["final_answer"] == REFUSAL and r["final_level"] == "BASSA"


# --------------------------------------------------------------- zero-regressione

class TestFallbackNarrative:
    def test_visual_senza_cablaggio_va_a_narrative(self):
        # visual_search=None: il ramo VISUAL non è cablato -> retrieval (NARRATIVE)
        called = {"retrieve": False}

        def retrieve(q, *a):
            called["retrieve"] = True
            return ([], [])
        answer("mostrami l'esploso del gruppo pompa", "tecnico",
               _deps(visual=None, retrieve=retrieve))
        assert called["retrieve"] is True
