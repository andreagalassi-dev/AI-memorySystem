"""Fase 1.3 Passo 5 — Test dello strumento visivo (funzioni pure + binder RBAC).

GPU-free e DB-free: `FakeVisualIndex` (Port 1.1) + un embedder fake iniettati. Copre:
il puntatore deterministico `format_visual_answer` (NESSUN LLM), la sua PARSABILITA'
(round-trip con `extract_citations`, cosi' il frontend estrae doc_stem+page), le
citazioni da `PageHit`, e il binder `make_visual_search` con RBAC fail-closed.
"""

from __future__ import annotations

from src.common.roles import Role
from src.graph.tools.visual_tool import (
    format_visual_answer,
    make_visual_search,
    visual_citations,
)
from src.retrieval.pixelrag.index import FakeVisualIndex, PageHit, VisualPoint
from src.retrieval.synthesis import extract_citations


class _FakeEmbedder:
    """Embedder fake: la query e' gia' il vettore (DB-free, deterministico)."""

    def __init__(self, qvec):
        self._qvec = qvec

    def embed_query(self, text: str):
        return self._qvec

    def embed_tiles(self, images):
        return []


def _page(doc="listino_x", page=3, score=0.9, n=2):
    return PageHit(doc=doc, page=page, score=score, n_tiles=n)


# --------------------------------------------------------- format_visual_answer

class TestFormatVisualAnswer:
    def test_contiene_puntatore_citazione_per_pagina(self):
        out = format_visual_answer([_page("listino_x", 3), _page("schema_y", 12)])
        assert "(listino_x, pag. 3)" in out
        assert "(schema_y, pag. 12)" in out

    def test_stringa_parsabile_dal_frontend_round_trip(self):
        # Requisito Passo 5: il frontend deve estrarre doc_stem+page dalla stringa.
        # La forma e' il pattern-citazione canonico -> extract_citations la parsa.
        pages = [_page("listino_x", 3), _page("schema_y", 12)]
        out = format_visual_answer(pages)
        assert extract_citations(out) == [("listino_x", 3), ("schema_y", 12)]

    def test_nessun_codice_nudo_nel_testo(self):
        # La risposta visiva non deve contenere codici fuori dalle citazioni:
        # cosi' il guardrail P3.1 (che legge le AFFERMAZIONI) resta un PASS.
        out = format_visual_answer([_page("V0123_manuale", 5)])
        # dopo aver tolto le citazioni, non resta nessun token code-shaped
        from src.retrieval.synthesis import strip_citations
        assert "V0123" not in strip_citations(out)


# --------------------------------------------------------- visual_citations

class TestVisualCitations:
    def test_citazioni_da_pagehit_non_da_llm(self):
        pages = [_page("a", 1), _page("b", 2)]
        assert visual_citations(pages) == [("a", 1), ("b", 2)]

    def test_vuoto(self):
        assert visual_citations([]) == []


# --------------------------------------------------------- make_visual_search

class TestMakeVisualSearch:
    def _index(self):
        idx = FakeVisualIndex()
        idx.upsert([
            VisualPoint(point_id="p1", doc="esploso_pompa", page=7, kind="tile",
                        row=0, col=0, vector=[1.0, 0.0],
                        payload={"ruoli_consentiti": ["tecnico", "commerciale"],
                                 "version_status": "current"}),
            VisualPoint(point_id="p2", doc="altro", page=2, kind="tile",
                        row=0, col=0, vector=[0.0, 1.0],
                        payload={"ruoli_consentiti": ["tecnico", "commerciale"],
                                 "version_status": "current"}),
        ])
        return idx

    def test_binder_ritorna_pagine_ordinate(self):
        search = make_visual_search(_FakeEmbedder([1.0, 0.0]), self._index(), k=3)
        pages = search("mostrami l'esploso della pompa", Role.TECNICO)
        assert pages and pages[0].doc == "esploso_pompa" and pages[0].page == 7

    def test_rbac_fail_closed_commerciale_non_vede_tile_tecnico(self):
        idx = FakeVisualIndex()
        idx.upsert([VisualPoint(point_id="solo_tecnico", doc="riservato", page=1,
                                kind="tile", row=0, col=0, vector=[1.0, 0.0],
                                payload={"ruoli_consentiti": ["tecnico"],
                                         "version_status": "current"})])
        search = make_visual_search(_FakeEmbedder([1.0, 0.0]), idx, k=3)
        assert search("q", Role.TECNICO)            # il tecnico la vede
        assert search("q", Role.COMMERCIALE) == []  # il commerciale NO -> nessun leak

    def test_solo_versione_current(self):
        idx = FakeVisualIndex()
        idx.upsert([VisualPoint(point_id="old", doc="vecchio", page=1, kind="tile",
                                row=0, col=0, vector=[1.0, 0.0],
                                payload={"ruoli_consentiti": ["tecnico"],
                                         "version_status": "superseded"})])
        search = make_visual_search(_FakeEmbedder([1.0, 0.0]), idx, k=3)
        assert search("q", Role.TECNICO) == []  # 'superseded' escluso di default
