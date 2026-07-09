"""Fase 1.1 — Test dell'adapter ColQwen2Embedder (backend MASCHERATO, no torch).

Le funzioni di embedding native sono iniettate/monkeypatchate: verifichiamo che
l'adapter rispetti il Protocol e deleghi correttamente, senza mai importare torch.
"""

from __future__ import annotations

from src.retrieval.pixelrag.embedder import ColQwen2Embedder, VisualEmbedder


class _SpyPages:
    def __init__(self):
        self.calls: list[list] = []

    def __call__(self, images):
        self.calls.append(images)
        return [[float(len(images))]] * len(images)   # un embedding fittizio per img


class TestContratto:
    def test_e_un_visual_embedder(self):
        assert isinstance(ColQwen2Embedder(embed_pages=lambda x: [],
                                           embed_query=lambda t: []), VisualEmbedder)


class TestDelega:
    def test_embed_tiles_delega_e_passa_le_immagini(self):
        spy = _SpyPages()
        emb = ColQwen2Embedder(embed_pages=spy, embed_query=lambda t: [])
        out = emb.embed_tiles(["imgA", "imgB"])
        assert spy.calls == [["imgA", "imgB"]]      # immagini passate cosi' come sono
        assert len(out) == 2

    def test_embed_tiles_lista_vuota_non_chiama_il_backend(self):
        spy = _SpyPages()
        emb = ColQwen2Embedder(embed_pages=spy, embed_query=lambda t: [])
        assert emb.embed_tiles([]) == []
        assert spy.calls == []                       # backend non invocato a vuoto

    def test_embed_query_delega(self):
        emb = ColQwen2Embedder(embed_pages=lambda x: [],
                               embed_query=lambda t: [len(t), 0.0])
        assert emb.embed_query("pompa") == [5, 0.0]


class TestDefaultLazy:
    def test_default_risolve_visual_solo_alla_chiamata(self, monkeypatch):
        # nessuna funzione iniettata: il default deve puntare a src.retrieval.visual,
        # risolto SOLO alla prima chiamata (torch mai importato qui, e' monkeypatchato)
        chiamate = {}

        def fake_embed_pages(images):
            chiamate["pages"] = images
            return [[1.0]] * len(images)

        monkeypatch.setattr("src.retrieval.visual.embed_pages", fake_embed_pages)
        emb = ColQwen2Embedder()                     # nessun import di visual/torch qui
        out = emb.embed_tiles(["x"])
        assert out == [[1.0]] and chiamate["pages"] == ["x"]
