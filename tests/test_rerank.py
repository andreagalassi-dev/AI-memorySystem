"""P2.8 — Test del reranker (logica di ordinamento, senza scaricare il modello)."""

from __future__ import annotations

from types import SimpleNamespace

import src.retrieval.rerank as rr


def _hit(text):
    return SimpleNamespace(payload={"text": text})


class TestRerank:
    def test_lista_vuota(self):
        assert rr.rerank("q", []) == []

    def test_ordina_per_score_e_tronca(self, monkeypatch):
        calls = {}

        def fake_reranker():
            class Tok:
                def __call__(self, pairs, **kw):
                    calls["pairs"] = pairs
                    return SimpleNamespace(to=lambda d: {"x": None})
            import torch

            class Model:
                def __call__(self, **kw):
                    return SimpleNamespace(
                        logits=torch.tensor([[0.1], [0.9], [0.5]]))
            return Tok(), Model(), "cpu"

        monkeypatch.setattr(rr, "_reranker", fake_reranker)
        hits = [_hit("a"), _hit("b"), _hit("c")]
        out = rr.rerank("query", hits, limit=2)
        assert [h.payload["text"] for h in out] == ["b", "c"]
        assert calls["pairs"] == [("query", "a"), ("query", "b"), ("query", "c")]
