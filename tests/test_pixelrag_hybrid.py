"""Fase 1.1 — Test dell'ibridazione: fusione RRF pura + routing per query_type.

DB-free: liste di chiavi (doc,page) e funzioni di ricerca fittizie/spia. Verifica la
formula RRF `1/(k+rank)`, la penalita' d'assenza, e che ogni corsia chiami solo i
retriever giusti.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.common.roles import Role
from src.retrieval.pixelrag.hybrid import RRF_K, FusedHit, hybrid_search, rrf_fuse


def _clf(query_type):
    """Classificatore fittizio: forza il ramo di routing."""
    return lambda q: SimpleNamespace(query_type=query_type)


class _Spy:
    def __init__(self, result):
        self.result = result
        self.calls: list[tuple] = []

    def __call__(self, query, role):
        self.calls.append((query, role))
        return self.result


# ------------------------------------------------------------------- rrf_fuse


class TestRRFFormula:
    def test_lista_singola_score_esatti(self):
        fused = rrf_fuse({"text": [("D", 1), ("D", 2)]})
        by = {(f.doc, f.page): f for f in fused}
        assert by[("D", 1)].score == pytest.approx(1 / (RRF_K + 1))
        assert by[("D", 2)].score == pytest.approx(1 / (RRF_K + 2))
        assert by[("D", 1)].sources == ("text",)

    def test_rank_precedente_pesa_di_piu(self):
        fused = rrf_fuse({"text": [("D", 1), ("D", 2), ("D", 3)]})
        assert [(f.doc, f.page) for f in fused] == [("D", 1), ("D", 2), ("D", 3)]

    def test_rrf_k_personalizzato(self):
        fused = rrf_fuse({"text": [("D", 1)]}, rrf_k=10)
        assert fused[0].score == pytest.approx(1 / 11)


class TestRRFPenalita:
    def test_presente_in_entrambe_batte_presente_in_una(self):
        # (A,1) e' in ENTRAMBE le liste, (B,1) solo nella visiva -> (A,1) vince
        # (penalita' d'assenza: (B,1) non riceve contributo dalla lista testo)
        fused = rrf_fuse({"text": [("A", 1)], "visual": [("B", 1), ("A", 1)]})
        top = fused[0]
        assert (top.doc, top.page) == ("A", 1)
        assert set(top.sources) == {"text", "visual"}
        b = next(f for f in fused if f.doc == "B")
        assert b.sources == ("visual",)
        assert top.score > b.score

    def test_score_e_somma_dei_contributi(self):
        fused = rrf_fuse({"text": [("A", 1)], "visual": [("A", 1)]})
        # A a rank 1 in entrambe -> 2 * 1/(k+1)
        assert fused[0].score == pytest.approx(2 / (RRF_K + 1))

    def test_dedup_nella_stessa_lista(self):
        # una chiave ripetuta nella stessa lista conta una sola volta (primo rank)
        fused = rrf_fuse({"text": [("A", 1), ("A", 1), ("B", 1)]})
        a = next(f for f in fused if f.doc == "A")
        assert a.score == pytest.approx(1 / (RRF_K + 1))   # non 2/(k+1)


# ------------------------------------------------------------------- routing


class TestRouting:
    def test_code_solo_bm25(self):
        text = _Spy([("D", 1)])
        vis = _Spy([("D", 9)])
        out = hybrid_search("AB-12345", role=Role.TECNICO, text_search=text,
                            visual_search=vis, classify=_clf("code"))
        assert len(text.calls) == 1 and len(vis.calls) == 0   # visivo NON invocato
        assert [(f.doc, f.page) for f in out] == [("D", 1)]
        assert out[0].sources == ("text",)

    def test_natural_solo_pixelrag(self):
        text = _Spy([("D", 1)])
        vis = _Spy([("D", 9)])
        out = hybrid_search("mostrami l'esploso", role=Role.TECNICO, text_search=text,
                            visual_search=vis, classify=_clf("natural"))
        assert len(vis.calls) == 1 and len(text.calls) == 0
        assert out[0].sources == ("visual",)

    def test_visual_solo_pixelrag(self):
        text = _Spy([("D", 1)])
        vis = _Spy([("D", 9)])
        hybrid_search("schema", role=Role.TECNICO, text_search=text,
                      visual_search=vis, classify=_clf("visual"))
        assert len(vis.calls) == 1 and len(text.calls) == 0

    def test_mixed_fonde_entrambi(self):
        text = _Spy([("D", 5), ("D", 1)])
        vis = _Spy([("D", 1), ("D", 7)])
        out = hybrid_search("valvola AB-1 esploso", role=Role.COMMERCIALE,
                            text_search=text, visual_search=vis, classify=_clf("mixed"))
        assert len(text.calls) == 1 and len(vis.calls) == 1
        # (D,1) compare in ENTRAMBE -> in cima con due sorgenti
        assert (out[0].doc, out[0].page) == ("D", 1)
        assert set(out[0].sources) == {"text", "visual"}

    def test_ruolo_propagato_ai_retriever(self):
        text = _Spy([("D", 1)])
        vis = _Spy([("D", 1)])
        hybrid_search("q", role=Role.AMMINISTRAZIONE, text_search=text,
                      visual_search=vis, classify=_clf("mixed"))
        assert text.calls[0][1] is Role.AMMINISTRAZIONE
        assert vis.calls[0][1] is Role.AMMINISTRAZIONE

    def test_cap_a_k(self):
        text = _Spy([("D", n) for n in range(1, 6)])
        out = hybrid_search("q", role=Role.TECNICO, text_search=text,
                            visual_search=_Spy([]), classify=_clf("code"), k=2)
        assert len(out) == 2


def test_fusedhit_e_una_dataclass():
    h = FusedHit(doc="D", page=1, score=0.5, sources=("text",))
    assert (h.doc, h.page, h.score, h.sources) == ("D", 1, 0.5, ("text",))
