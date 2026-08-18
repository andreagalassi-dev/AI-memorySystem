"""RIORDINO delle pagine visive col giudice — l'ALTRO uso dello stesso strumento.

Il giudice pagina (`src/graph/tools/page_judge.py`) nasce come VETO: risponde
"nessuna e' pertinente" e il ramo visivo degrada a "non lo so". Misurato in
perdita sul metro del progetto, resta SPENTO (`AIKE_VISUAL_PAGE_JUDGE`).

Qui si prova l'uso opposto, che non e' un veto ma una PREFERENZA: le stesse
pagine restano tutte a schermo, cambia solo quale sta in cima. La proprieta'
che rende il riordino sicuro per costruzione, ed e' quella che questi test
inchiodano, e':

    l'INSIEME delle pagine mostrate non cambia MAI.

E' l'equivalente unitario del controllo di misura (`recall@3`/`recall@5`
identici cifra per cifra): se l'insieme cambia, non stiamo riordinando —
stiamo filtrando, ed e' sbagliato.

Invarianti verificati qui:
  - promozione STABILE: la pagina indicata va in cima, le altre mantengono
    l'ordine relativo del retrieval;
  - il giudice non ha potere di veto in questa modalita': `NESSUNA` non toglie
    nulla, lascia l'ordine del retrieval;
  - fail-closed su ogni imprevisto (eccezione, indice fuori range, esito
    malformato) -> ordine originale, mai un'eccezione che rompe la risposta;
  - la modalita' di RIFIUTO resta il default e resta invariata;
  - ogni riordino lascia una riga di audit con posizione di partenza e arrivo.

GPU-free, DB-free, rete-free: LLM finto.
"""

from __future__ import annotations

import pytest

from src.graph.llm import FakeLLM
from src.graph.pipeline import Deps, answer, visual_node
from src.graph.tools.page_judge import (JUDGE_MODE_REFUSE, JUDGE_MODE_REORDER,
                                        REFUSE_NONE_TAG, REORDER_TAG,
                                        JudgeOutcome, make_page_judge,
                                        promote_chosen)
from src.retrieval.confidence import Thresholds
from src.retrieval.pixelrag.index import PageHit
from src.retrieval.synthesis import extract_citations

_T = Thresholds(tau_hi=0.5, tau_lo=0.2)


def _pages():
    """Tre pagine, come ne mostra il ramo visivo (VISUAL_K=3)."""
    return [PageHit(doc="gehl_v270_parts", page=12, score=0.90, n_tiles=2),
            PageHit(doc="gehl_v270_parts", page=33, score=0.80, n_tiles=1),
            PageHit(doc="gehl_v270_parts", page=94, score=0.70, n_tiles=1)]


def _deps(*, visual=None, floor=None, judge=None, mode=JUDGE_MODE_REORDER):
    return Deps(
        retrieve=lambda q, *a: ([], []),
        synthesize=lambda q, ev: "n/a",
        catalog_lookup=lambda k: False,
        thresholds=_T,
        visual_search=visual,
        visual_score_floor=floor,
        page_judge=judge,
        page_judge_mode=mode,
    )


def _keys(pages):
    return [(p.doc, p.page) for p in pages]


# ------------------------------------------------- la funzione pura di riordino

class TestPromoteChosen:
    def test_promuove_la_terza_e_lascia_le_altre_in_ordine(self):
        pages = _pages()
        out, moved_from = promote_chosen(pages, JudgeOutcome("CONFIRM", 2, "C"))
        assert _keys(out) == [("gehl_v270_parts", 94),
                              ("gehl_v270_parts", 12),
                              ("gehl_v270_parts", 33)]
        assert moved_from == 2

    def test_insieme_invariato_e_la_proprieta_che_conta(self):
        pages = _pages()
        for idx in (0, 1, 2):
            out, _ = promote_chosen(pages, JudgeOutcome("CONFIRM", idx, "x"))
            # stesse pagine, stessa cardinalita': nessuna tolta, nessuna aggiunta.
            # E' l'invariante che tiene identici recall@3 e recall@5.
            assert sorted(_keys(out)) == sorted(_keys(pages))
            assert len(out) == len(pages)

    def test_prima_gia_in_cima_nessun_movimento(self):
        pages = _pages()
        out, moved_from = promote_chosen(pages, JudgeOutcome("CONFIRM", 0, "A"))
        assert _keys(out) == _keys(pages)
        assert moved_from is None      # niente da tracciare: non e' un riordino

    def test_nessuna_non_toglie_nulla(self):
        # In questa modalita' il giudice NON ha potere di veto: NESSUNA vale
        # "nessuna preferenza", non "butta via la risposta".
        pages = _pages()
        out, moved_from = promote_chosen(pages, JudgeOutcome("REFUSE", None, "NESSUNA"))
        assert _keys(out) == _keys(pages) and moved_from is None

    def test_skip_lascia_ordine_del_retrieval(self):
        pages = _pages()
        out, moved_from = promote_chosen(pages, JudgeOutcome("SKIP", None, "LLM giu'"))
        assert _keys(out) == _keys(pages) and moved_from is None

    def test_esito_assente_lascia_ordine_del_retrieval(self):
        pages = _pages()
        out, moved_from = promote_chosen(pages, None)
        assert _keys(out) == _keys(pages) and moved_from is None

    @pytest.mark.parametrize("idx", [3, 7, -1, -99])
    def test_indice_fuori_range_lascia_ordine_del_retrieval(self, idx):
        pages = _pages()
        out, moved_from = promote_chosen(pages, JudgeOutcome("CONFIRM", idx, "?"))
        assert _keys(out) == _keys(pages) and moved_from is None

    @pytest.mark.parametrize("idx", ["1", 1.0, True, object()])
    def test_indice_malformato_lascia_ordine_del_retrieval(self, idx):
        # fail-closed sul TIPO: un esito costruito male non deve riordinare a caso
        pages = _pages()
        out, moved_from = promote_chosen(pages, JudgeOutcome("CONFIRM", idx, "?"))
        assert _keys(out) == _keys(pages) and moved_from is None

    def test_non_muta_la_lista_in_ingresso(self):
        pages = _pages()
        promote_chosen(pages, JudgeOutcome("CONFIRM", 2, "C"))
        assert _keys(pages) == [("gehl_v270_parts", 12),
                                ("gehl_v270_parts", 33),
                                ("gehl_v270_parts", 94)]

    def test_lista_vuota_non_esplode(self):
        out, moved_from = promote_chosen([], JudgeOutcome("CONFIRM", 0, "A"))
        assert out == [] and moved_from is None


# ------------------------------------------------------- innesto nel grafo

class TestVisualNodeRiordino:
    def _run(self, judge, **kw):
        return visual_node({"query": "wiring harness dello chassis", "role": "tecnico"},
                           _deps(visual=lambda q, r: _pages(), judge=judge, **kw))

    def test_la_terza_indicata_esce_per_prima(self):
        out = self._run(lambda q, pages: JudgeOutcome("CONFIRM", 2, "C"))
        assert out["level"] == "MEDIA"
        assert out["citations"] == [("gehl_v270_parts", 94),
                                    ("gehl_v270_parts", 12),
                                    ("gehl_v270_parts", 33)]

    def test_le_pagine_mostrate_restano_le_stesse(self):
        out = self._run(lambda q, pages: JudgeOutcome("CONFIRM", 2, "C"))
        assert sorted(out["citations"]) == sorted(_keys(_pages()))
        assert len(out["visual_pages"]) == 3

    def test_audit_traccia_partenza_e_arrivo(self):
        out = self._run(lambda q, pages: JudgeOutcome("CONFIRM", 2, "C"))
        riga = [a for a in out["audit"] if REORDER_TAG in a]
        assert len(riga) == 1
        assert "p.94" in riga[0]
        assert "posizione 3" in riga[0] and "posizione 1" in riga[0]

    def test_nessun_riordino_nessuna_riga_di_riordino(self):
        out = self._run(lambda q, pages: JudgeOutcome("CONFIRM", 0, "A"))
        assert not any(REORDER_TAG in a for a in out["audit"])
        assert out["citations"] == _keys(_pages())

    def test_nessuna_ordine_invariato_e_nessun_rifiuto(self):
        out = self._run(lambda q, pages: JudgeOutcome("REFUSE", None, "NESSUNA"))
        assert out["level"] == "MEDIA"                    # nessun veto qui
        assert out["citations"] == _keys(_pages())
        assert not any(REFUSE_NONE_TAG in a for a in out["audit"])

    def test_giudice_che_solleva_ordine_invariato(self):
        def boom(q, pages):
            raise RuntimeError("crash imprevisto del giudice")
        out = self._run(boom)
        assert out["level"] == "MEDIA"
        assert out["citations"] == _keys(_pages())

    def test_indice_inesistente_ordine_invariato(self):
        out = self._run(lambda q, pages: JudgeOutcome("CONFIRM", 9, "?"))
        assert out["level"] == "MEDIA"
        assert out["citations"] == _keys(_pages())

    def test_skip_ordine_invariato(self):
        out = self._run(lambda q, pages: JudgeOutcome("SKIP", None, "cartiglio assente"))
        assert out["level"] == "MEDIA"
        assert out["citations"] == _keys(_pages())

    def test_giudice_non_interrogato_sotto_soglia_score(self):
        called = {"n": 0}

        def judge(q, pages):
            called["n"] += 1
            return JudgeOutcome("CONFIRM", 2, "C")
        weak = [PageHit(doc="d", page=1, score=0.10, n_tiles=1)]
        out = visual_node({"query": "q", "role": "tecnico"},
                          _deps(visual=lambda q, r: weak, floor=0.20, judge=judge))
        # il pavimento di sicurezza viene PRIMA: niente pagine, niente da riordinare
        assert out["level"] == "BASSA" and called["n"] == 0

    def test_e2e_la_pagina_promossa_e_la_prima_citata(self):
        judge = lambda q, pages: JudgeOutcome("CONFIRM", 2, "C")
        res = answer("mostrami l'esploso del wiring harness", "tecnico",
                     _deps(visual=lambda q, r: _pages(), judge=judge))
        assert res["final_level"] == "MEDIA"
        cited = extract_citations(res["final_answer"])
        assert cited[0] == ("gehl_v270_parts", 94)
        assert sorted(cited) == sorted(_keys(_pages()))   # nessuna persa


class TestRiordinoConGiudiceReale:
    """Stesso binder di produzione (`make_page_judge`), LLM finto: nessuna rete."""

    def test_llm_che_risponde_C_promuove_la_terza(self):
        llm = FakeLLM("C")
        judge = make_page_judge(llm, get_title=lambda doc, page: f"FIG {page}")
        out = visual_node({"query": "wiring harness", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge))
        assert out["citations"][0] == ("gehl_v270_parts", 94)
        assert sorted(out["citations"]) == sorted(_keys(_pages()))

    def test_llm_giu_ordine_invariato(self):
        judge = make_page_judge(FakeLLM(error=RuntimeError("ollama giu'")),
                                get_title=lambda doc, page: f"FIG {page}")
        out = visual_node({"query": "wiring harness", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge))
        assert out["level"] == "MEDIA"
        assert out["citations"] == _keys(_pages())

    def test_llm_che_delira_ordine_invariato(self):
        # fuori dall'insieme chiuso -> NESSUNA -> in riordino: nessuna preferenza
        judge = make_page_judge(FakeLLM("secondo me la pagina giusta e' la 94"),
                                get_title=lambda doc, page: f"FIG {page}")
        out = visual_node({"query": "wiring harness", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge))
        assert out["level"] == "MEDIA"
        assert out["citations"] == _keys(_pages())


# --------------------------------------------- il rifiuto NON e' stato toccato

class TestModalitaRifiutoInvariata:
    def test_il_default_e_il_rifiuto(self):
        """Chi costruisce `Deps` senza dichiarare la modalita' ottiene ESATTAMENTE
        il comportamento di prima: il riordino e' opt-in, non un cambio silenzioso."""
        deps = Deps(retrieve=lambda q, *a: ([], []), synthesize=lambda q, ev: "n/a",
                    catalog_lookup=lambda k: False, thresholds=_T)
        assert deps.page_judge_mode == JUDGE_MODE_REFUSE

    def test_in_modalita_rifiuto_nessuna_rifiuta_ancora(self):
        judge = lambda q, pages: JudgeOutcome("REFUSE", None, "nessuna pertinente")
        out = visual_node({"query": "q", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge,
                                mode=JUDGE_MODE_REFUSE))
        assert out["level"] == "BASSA" and out["visual_pages"] == []
        assert any(REFUSE_NONE_TAG in a for a in out["audit"])

    def test_in_modalita_rifiuto_non_si_riordina(self):
        judge = lambda q, pages: JudgeOutcome("CONFIRM", 2, "C")
        out = visual_node({"query": "q", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge,
                                mode=JUDGE_MODE_REFUSE))
        assert out["citations"] == _keys(_pages())
        assert not any(REORDER_TAG in a for a in out["audit"])


class TestConfigurazione:
    def test_riordino_default_acceso(self, monkeypatch):
        """Default ON: misurato in guadagno (recall@1 0.333->0.467) e senza
        perdita possibile (l'insieme delle pagine non cambia). L'opposto del
        VETO, che resta OFF perche' misurato in perdita."""
        monkeypatch.delenv("AIKE_VISUAL_PAGE_REORDER", raising=False)
        from src.serving.session_deps import _page_reorder_enabled
        assert _page_reorder_enabled() is True

    @pytest.mark.parametrize("val,expected", [
        ("0", False), ("false", False), ("FALSE", False),
        ("1", True), ("true", True), ("", True), ("boh", True),
    ])
    def test_env_commuta_il_riordino(self, monkeypatch, val, expected):
        monkeypatch.setenv("AIKE_VISUAL_PAGE_REORDER", val)
        from src.serving.session_deps import _page_reorder_enabled
        assert _page_reorder_enabled() is expected

    def test_rifiuto_e_riordino_sono_due_interruttori_distinti(self, monkeypatch):
        monkeypatch.setenv("AIKE_VISUAL_PAGE_REORDER", "1")
        monkeypatch.delenv("AIKE_VISUAL_PAGE_JUDGE", raising=False)
        from src.serving.session_deps import (_page_judge_enabled,
                                              _page_judge_mode,
                                              _page_reorder_enabled)
        assert _page_reorder_enabled() is True
        assert _page_judge_enabled() is False       # il veto resta spento
        assert _page_judge_mode() == JUDGE_MODE_REORDER

    def test_il_veto_vince_se_qualcuno_accende_entrambi(self, monkeypatch):
        """Due usi dello stesso strumento, una sola chiamata all'LLM. Se entrambi
        sono accesi vince la modalita' di SICUREZZA (il veto), che e' l'unica che
        puo' impedire una risposta sbagliata."""
        monkeypatch.setenv("AIKE_VISUAL_PAGE_REORDER", "1")
        monkeypatch.setenv("AIKE_VISUAL_PAGE_JUDGE", "1")
        from src.serving.session_deps import _page_judge_mode
        assert _page_judge_mode() == JUDGE_MODE_REFUSE

    def test_modalita_nel_blocco_config(self, monkeypatch):
        monkeypatch.setenv("AIKE_VISUAL_PAGE_REORDER", "1")
        monkeypatch.setenv("AIKE_VISUAL_PAGE_JUDGE", "0")
        from src.common.run_config import effective_config, format_config_banner
        cfg = effective_config()
        assert cfg["visual_page_judge"]["mode"] == JUDGE_MODE_REORDER
        assert cfg["visual_page_judge"]["enabled"] is True
        assert "riordino" in format_config_banner(cfg).lower()

    def test_config_osserva_la_modalita_dalle_deps(self):
        cfg_deps = _deps(visual=lambda q, r: [],
                         judge=lambda q, p: JudgeOutcome("SKIP", None, ""))
        from src.common.run_config import effective_config
        cfg = effective_config(cfg_deps)
        assert cfg["visual_page_judge"]["enabled"] is True
        assert cfg["visual_page_judge"]["mode"] == JUDGE_MODE_REORDER
        assert cfg["visual_page_judge"]["source"] == "osservato"
