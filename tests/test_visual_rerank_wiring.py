"""Cablaggio del reranker visivo in produzione: pool bounded, no-drop-silenzioso,
binder e gate hardware. Tutto DB-free / no-rete / senza VLM reale.
"""

from __future__ import annotations

import logging
import shutil

import pytest

from src.common.roles import Role
from src.retrieval.pixelrag.index import (
    KIND_TILE,
    FakeVisualIndex,
    PageHit,
    VisualPoint,
)
from src.retrieval.pixelrag.reranker import FakeVisualReranker, Qwen3VLReranker
from src.retrieval.pixelrag.search import RERANK_POOL, visual_search


class _FakeEmbedder:
    def __init__(self, qvec):
        self.qvec = qvec

    def embed_query(self, text):
        return self.qvec

    def embed_tiles(self, images):
        return []


def _pt(pid, doc, page, vec):
    return VisualPoint(point_id=pid, doc=doc, page=page, kind=KIND_TILE, row=0, col=0,
                       vector=vec, payload={"ruoli_consentiti": ["tecnico"],
                                            "version_status": "current"})


# --------------------------------------------------------- TASK 1: pool bounded

class _RecordingReranker(FakeVisualReranker):
    """FakeVisualReranker che registra la dimensione del pool ricevuto."""

    def __init__(self):
        self.pool_len = None

    def rerank(self, query, pages, top_k):
        self.pool_len = len(pages)
        return super().rerank(query, pages, top_k)


class TestPoolBounded:
    def test_pool_al_piu_rerank_pool(self):
        # 20 pagine distinte candidate: il pool passato al reranker deve essere <= 12.
        idx = FakeVisualIndex()
        idx.upsert([_pt(f"p{n}", "D", n, [1.0, 0.0]) for n in range(1, 21)])
        rr = _RecordingReranker()

        visual_search("x", embedder=_FakeEmbedder([1.0, 0.0]), index=idx,
                      role=Role.TECNICO, k=3, reranker=rr)

        assert rr.pool_len is not None
        assert rr.pool_len <= RERANK_POOL
        assert rr.pool_len == RERANK_POOL  # con 20 candidati il pool si satura a 12


# ----------------------------------------------- TASK 2: reranker no-drop + ordine

class TestRerankerNoSilentDrop:
    def test_png_e_pdf_mancanti_logga_warning_e_non_crasha(self, monkeypatch, caplog):
        # ensure_page_render mockato (nessun accesso al corpus): resta senza render.
        import src.ui.page_render as pr
        monkeypatch.setattr(pr, "ensure_page_render", lambda doc, page: None)

        rk = Qwen3VLReranker()
        pages = [PageHit(doc="___doc_inesistente___", page=1, score=0.5, n_tiles=1)]

        with caplog.at_level(logging.WARNING):
            out = rk.rerank("q", pages, top_k=3)

        assert out == []  # nessuna pagina rerankabile, ma nessun crash
        assert any("render mancante" in r.message and "___doc_inesistente___" in r.message
                   for r in caplog.records)

    def test_png_presenti_ordine_per_score_decrescente(self, monkeypatch, tmp_path):
        # Scriviamo PNG reali sotto la RENDERS dir (in output/, gitignored) e mockiamo
        # SOLO il modello: nessun VLM caricato.
        from pathlib import Path
        from PIL import Image

        ROOT = Path(__file__).resolve().parent.parent
        renders = ROOT / "output" / "visual" / "renders"
        doc = "__rerank_test_doc__"
        doc_dir = renders / doc
        doc_dir.mkdir(parents=True, exist_ok=True)
        try:
            for page in (1, 2, 3):
                Image.new("RGB", (32, 32), (page, page, page)).save(
                    doc_dir / f"p{page:04d}.png")

            # Modello fake: assegna score crescente col numero di pagina.
            class _FakeModel:
                def predict(self, pairs, **kw):
                    # ordine di pairs == ordine di valid_pages (pagine 1,2,3)
                    return [0.1, 0.9, 0.5]

            rk = Qwen3VLReranker()
            monkeypatch.setattr(rk, "_get_model", lambda: _FakeModel())

            pages = [PageHit(doc=doc, page=p, score=0.0, n_tiles=1) for p in (1, 2, 3)]
            out = rk.rerank("q", pages, top_k=3)

            assert [(p.page, p.score) for p in out] == [(2, 0.9), (3, 0.5), (1, 0.1)]
        finally:
            shutil.rmtree(doc_dir, ignore_errors=True)


# ----------------------------------------------------- TASK 4: gate hardware

class TestGate:
    def _reset(self, sd):
        sd._CACHE.clear()
        sd._llm = None
        sd._visual_search = None
        sd._visual_reranker = None
        sd._multiplexer = None

    def _stub_heavy(self, monkeypatch):
        """Stubba LLM/qdrant/embedder e cattura il reranker passato al binder."""
        import src.serving.session_deps as sd
        captured = {}

        # LLM: niente rete, niente Ollama reale.
        monkeypatch.setattr("src.backend.clients.ollama_client.OllamaModel",
                            lambda *a, **k: object())
        import requests
        monkeypatch.setattr(requests, "get",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net")))

        # Visual: qdrant/embedder stubbati; il binder cattura il reranker.
        monkeypatch.setattr("src.backend.clients.qdrant_client.get_qdrant_client",
                            lambda: object())
        monkeypatch.setattr("src.retrieval.pixelrag.embedder.Qwen3VLEmbedder",
                            lambda *a, **k: object())
        monkeypatch.setattr("src.retrieval.pixelrag.index.QdrantVisualIndex",
                            lambda *a, **k: object())
        monkeypatch.setattr("src.retrieval.pixelrag.reranker.Qwen3VLReranker",
                            lambda *a, **k: "RERANKER_SENTINEL")

        def fake_binder(embedder, index, *, k=3, reranker=None):
            captured["reranker"] = reranker
            return lambda q, role: []
        monkeypatch.setattr("src.graph.tools.visual_tool.make_visual_search", fake_binder)

        self._reset(sd)
        return sd, captured

    def test_gate_off_binder_riceve_none(self, monkeypatch):
        monkeypatch.setenv("AIKE_VISUAL_RERANK", "0")
        sd, captured = self._stub_heavy(monkeypatch)
        sd._init_shared_singletons()
        assert captured["reranker"] is None
        assert sd._visual_reranker is None

    def test_gate_on_binder_riceve_reranker(self, monkeypatch):
        monkeypatch.setenv("AIKE_VISUAL_RERANK", "1")
        sd, captured = self._stub_heavy(monkeypatch)
        sd._init_shared_singletons()
        assert captured["reranker"] == "RERANKER_SENTINEL"
        assert sd._visual_reranker == "RERANKER_SENTINEL"

    def test_gate_auto_soglia_ram(self, monkeypatch):
        import src.serving.session_deps as sd
        monkeypatch.setenv("AIKE_VISUAL_RERANK", "auto")
        # soglia irraggiungibile -> OFF ; soglia bassissima -> ON
        monkeypatch.setenv("AIKE_RERANK_MIN_GB", "100000")
        assert sd._visual_rerank_enabled() is False
        monkeypatch.setenv("AIKE_RERANK_MIN_GB", "0")
        assert sd._visual_rerank_enabled() is True

    def test_gate_esplicito_ignora_ram(self, monkeypatch):
        import src.serving.session_deps as sd
        monkeypatch.setenv("AIKE_RERANK_MIN_GB", "100000")  # RAM insufficiente
        monkeypatch.setenv("AIKE_VISUAL_RERANK", "true")
        assert sd._visual_rerank_enabled() is True
        monkeypatch.setenv("AIKE_VISUAL_RERANK", "false")
        assert sd._visual_rerank_enabled() is False
