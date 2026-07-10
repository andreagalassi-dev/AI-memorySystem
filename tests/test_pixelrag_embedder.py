"""Fase 1.1 — Test degli adapter visuali (ColQwen2 + Qwen3-VL), backend MASCHERATO.

Le funzioni di embedding native sono iniettate/monkeypatchate: verifichiamo che
gli adapter rispettino il Protocol e deleghino correttamente, senza mai importare
torch. Per Qwen3VLEmbedder verifichiamo anche l'hook al ModelMultiplexer
(eviction-before-load: il Watchdog sgombra i modelli inattivi prima di caricare
il VLM, PIANO-IMPLEMENTAZIONE riga 6).
"""

from __future__ import annotations

from src.retrieval.pixelrag.embedder import (
    ColQwen2Embedder,
    Qwen3VLEmbedder,
    VisualEmbedder,
)


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


# ------------------------------------------------------------------ Qwen3-VL


class _SpyPagesQ3:
    def __init__(self):
        self.calls: list[list] = []

    def __call__(self, images):
        self.calls.append(images)
        return [[float(len(images))]] * len(images)


class _FakeMultiplexer:
    """Multiplexer di test: registra use() e resident() come il reale, senza runtime.

    Mantiene l'ordinamento LRU come `ModelMultiplexer` (riga 0 = LRU) cosi' i test
    sull'eviction sono fedeli: use() di un nuovo modello con max_resident=1 deve
    evictare il precedente.
    """

    def __init__(self, max_resident: int = 1):
        self.max_resident = max_resident
        self._lru: list[str] = []
        self.evicted: list[str] = []

    def use(self, name, num_ctx=None):
        if name in self._lru:
            self._lru.remove(name)
            self._lru.append(name)
            return 16000
        while len(self._lru) >= self.max_resident:
            victim = self._lru.pop(0)
            self.evicted.append(victim)
        self._lru.append(name)
        return 16000

    def resident(self):
        return set(self._lru)


class TestQwen3Contratto:
    def test_e_un_visual_embedder(self):
        assert isinstance(Qwen3VLEmbedder(embed_pages=lambda x: [],
                                         embed_query=lambda t: []), VisualEmbedder)

    def test_model_name_parametrico_non_hardcodato(self):
        emb = Qwen3VLEmbedder(embed_pages=lambda x: [], embed_query=lambda t: [],
                              model_name="org/custom-vl-emb")
        assert emb.model_name == "org/custom-vl-emb"

    def test_default_model_name_è_placeholder_non_verificato(self):
        emb = Qwen3VLEmbedder(embed_pages=lambda x: [], embed_query=lambda t: [])
        # placeholder, NON un ID HuggingFace verificato: lo spike P1 lo binda
        assert emb.model_name and not emb.model_name.startswith("Qwen/")
        assert "/" not in emb.model_name  # non e' un HF id reale (spike-pending)


class TestQwen3Delega:
    def test_embed_tiles_delega_e_passa_le_immagini(self):
        spy = _SpyPagesQ3()
        emb = Qwen3VLEmbedder(embed_pages=spy, embed_query=lambda t: [])
        out = emb.embed_tiles(["imgA", "imgB"])
        assert spy.calls == [["imgA", "imgB"]]
        assert len(out) == 2

    def test_embed_tiles_lista_vuota_non_chiama_backend_né_multiplexer(self):
        spy = _SpyPagesQ3()
        mux = _FakeMultiplexer()
        emb = Qwen3VLEmbedder(embed_pages=spy, embed_query=lambda t: [],
                              multiplexer=mux)
        assert emb.embed_tiles([]) == []
        assert spy.calls == []          # backend non invocato a vuoto
        assert mux.resident() == set()  # nemmeno il multiplexer toccato

    def test_embed_query_delega(self):
        emb = Qwen3VLEmbedder(embed_pages=lambda x: [],
                              embed_query=lambda t: [len(t), 0.0])
        assert emb.embed_query("pompa") == [5, 0.0]


class TestQwen3MultiplexerHook:
    """Invariante Watchdog (PIANO-IMPLEMENTAZIONE riga 6): prima di caricare il
    VLM il Multiplexer evicta i modelli inattivi (LRU). L'hook deve scattare
    PRIMA dell'inferenza, non dopo.
    """

    def test_use_chiamato_prima_del_backend_su_embed_tiles(self):
        ordine = []
        spy = _SpyPagesQ3()
        spy._orig_call = spy.__call__

        def recording(images):
            ordine.append("backend")
            return spy._orig_call(images)

        mux = _FakeMultiplexer(max_resident=1)
        mux._orig_use = mux.use

        def recording_use(name, num_ctx=None):
            ordine.append(f"use:{name}")
            return mux._orig_use(name, num_ctx)

        # simula Gemma 4 gia' residente (sara' la vittima LRU) SENZA recording
        mux._orig_use("gemma-4-12b")
        # ora attacchiamo il recording e resettiamo l'ordine: da qui in poi
        # contiamo solo le chiamate dell'embedder
        mux.use = recording_use
        ordine.clear()
        emb = Qwen3VLEmbedder(embed_pages=recording, embed_query=lambda t: [],
                              multiplexer=mux, model_name="qwen-vl-embedding")
        emb.embed_tiles(["img"])
        # l'hook del multiplexer deve scattare PRIMA dell'inferenza torch
        assert ordine[0] == "use:qwen-vl-embedding"
        assert ordine[1] == "backend"

    def test_use_evicta_modelli_inattivi_lru(self):
        mux = _FakeMultiplexer(max_resident=1)
        mux.use("gemma-4-12b")  # modello inattivo residente
        emb = Qwen3VLEmbedder(embed_pages=lambda x: [[1.0]] * len(x),
                              embed_query=lambda t: [0.0],
                              multiplexer=mux, model_name="qwen-vl-embedding")
        emb.embed_query("testo")
        # Gemma 4 e' stato evictato per fare spazio al VLM
        assert "gemma-4-12b" in mux.evicted
        assert "qwen-vl-embedding" in mux.resident()
        assert "gemma-4-12b" not in mux.resident()

    def test_use_idempotente_anti_churn(self):
        mux = _FakeMultiplexer(max_resident=2)
        emb = Qwen3VLEmbedder(embed_pages=lambda x: [[1.0]] * len(x),
                              embed_query=lambda t: [0.0],
                              multiplexer=mux, model_name="qwen-vl-embedding")
        emb.embed_query("a")  # primo load
        emb.embed_query("b")  # gia' residente: nessun nuovo use() con reload
        assert mux.resident() == {"qwen-vl-embedding"}
        assert mux.evicted == []

    def test_senza_multiplexer_no_op(self):
        # path legacy / test unitari: nessun multiplexer -> nessun crash
        emb = Qwen3VLEmbedder(embed_pages=lambda x: [[1.0]] * len(x),
                              embed_query=lambda t: [len(t)])
        assert emb.embed_query("ciao") == [4]


class TestQwen3DefaultLazy:
    def test_default_risolve_qwen3vl_solo_alla_chiamata(self, monkeypatch):
        chiamate = {}

        def fake_embed_pages(images):
            chiamate["pages"] = images
            return [[1.0]] * len(images)

        # il default punta a src.retrieval.qwen3vl (NON visual/ColQwen2)
        monkeypatch.setattr("src.retrieval.qwen3vl.embed_pages", fake_embed_pages)
        emb = Qwen3VLEmbedder()  # nessun import di qwen3vl/torch qui
        out = emb.embed_tiles(["x"])
        assert out == [[1.0]] and chiamate["pages"] == ["x"]

    def test_default_query_risolve_qwen3vl(self, monkeypatch):
        monkeypatch.setattr("src.retrieval.qwen3vl.embed_query",
                            lambda t: [float(len(t))])
        emb = Qwen3VLEmbedder()
        assert emb.embed_query("pompa") == [5.0]
