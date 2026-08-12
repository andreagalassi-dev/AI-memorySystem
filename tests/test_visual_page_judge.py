"""Verificatore di pertinenza della pagina nel ramo VISUAL (anti confident-wrong).

Il ramo visivo rispondeva MEDIA mostrando la pagina sbagliata senza mai dire
"non lo so": nessuna soglia numerica lo risolve (misurato — gli score di pagine
giuste e sbagliate sono sovrapposti, e la risposta sbagliata peggiore ha lo score
piu' alto dell'intero set). Il segnale diverso e' il CARTIGLIO della pagina letto
dal livello testo del PDF, giudicato da un modello TESTUALE a scelta chiusa.

Invarianti verificati qui:
  - il verificatore puo' SOLO aggiungere rifiuti, mai promuovere e mai riordinare;
  - degrado PERMISSIVO sull'infrastruttura (PDF assente, cartiglio vuoto, LLM giu'
    o in timeout -> nessun blocco: un guasto non deve fabbricare rifiuti);
  - fail-closed sul PARSING (risposta fuori dall'insieme chiuso -> NESSUNA);
  - ogni decisione lascia una riga di audit.

GPU-free, DB-free, LLM-free: PDF sintetici con PyMuPDF e `FakeLLM`.
"""

from __future__ import annotations

import pytest

import fitz

from src.graph.llm import FakeLLM
from src.graph.pipeline import Deps, answer, visual_node
from src.graph.tools.page_judge import (OTHER_PAGE_TAG, REFUSE_NONE_TAG,
                                        JudgeOutcome, build_judge_prompt,
                                        make_page_judge, parse_judge_verdict)
from src.retrieval.confidence import Thresholds
from src.retrieval.pixelrag.index import PageHit
from src.retrieval.synthesis import REFUSAL, extract_citations

_T = Thresholds(tau_hi=0.5, tau_lo=0.2)


def _pages():
    return [PageHit(doc="branson_4520R_parts", page=69, score=0.9, n_tiles=2),
            PageHit(doc="branson_4520R_parts", page=22, score=0.7, n_tiles=1)]


def _deps(*, visual=None, floor=None, judge=None):
    return Deps(
        retrieve=lambda q, *a: ([], []),
        synthesize=lambda q, ev: "n/a",
        catalog_lookup=lambda k: False,
        thresholds=_T,
        visual_search=visual,
        visual_score_floor=floor,
        page_judge=judge,
    )


# ------------------------------------------------- estrazione del cartiglio

@pytest.fixture()
def fake_corpus(tmp_path, monkeypatch):
    """Due PDF sintetici: uno con livello testo, uno senza (solo un rettangolo)."""
    import src.retrieval.page_title as pt

    withtext = tmp_path / "doc_con_testo.pdf"
    d = fitz.open()
    p = d.new_page()
    p.insert_text((72, 100), "FIG S040 4520R CLUTCH RELEASE")
    p2 = d.new_page()
    # pagina-tabella: testo lunghissimo, piu' i numeri-pallino di un esploso
    p2.insert_text((36, 60), "CLUTCH ASSY PARTS LIST\n" + "\n".join(
        f"{i} 4520R-{i:04d} BOLT FLANGE M{i}" for i in range(1, 60)), fontsize=6)
    p3 = d.new_page()
    p3.insert_text((72, 100), "1 2 3 4 5")   # solo numeri-pallino: nessun cartiglio
    d.save(withtext)
    d.close()

    notext = tmp_path / "doc_scansione.pdf"
    d = fitz.open()
    d.new_page().draw_rect(fitz.Rect(10, 10, 100, 100))
    d.save(notext)
    d.close()

    mapping = {"doc_con_testo": withtext, "doc_scansione": notext}
    monkeypatch.setattr(pt, "resolve_pdf", lambda stem: mapping.get(stem))
    pt.clear_page_title_cache()
    yield mapping
    pt.clear_page_title_cache()


class TestExtractPageTitle:
    def test_cartiglio_dal_livello_testo(self, fake_corpus):
        from src.retrieval.page_title import extract_page_title
        assert extract_page_title("doc_con_testo", 1) == "FIG S040 4520R CLUTCH RELEASE"

    def test_pagina_tabella_troncata_al_tetto(self, fake_corpus):
        from src.retrieval.page_title import PAGE_TITLE_MAX_CHARS, extract_page_title
        t = extract_page_title("doc_con_testo", 2)
        assert t is not None
        # il troncamento dev'essere DAVVERO esercitato: la pagina di prova e' piu'
        # lunga del tetto, quindi l'etichetta ci arriva esatta e parte dal titolo
        assert len(t) == PAGE_TITLE_MAX_CHARS
        assert t.startswith("CLUTCH ASSY PARTS LIST")

    def test_solo_numeri_nessun_cartiglio(self, fake_corpus):
        from src.retrieval.page_title import extract_page_title
        # i numeri-pallino di un esploso non sono un cartiglio: None, non rumore
        assert extract_page_title("doc_con_testo", 3) is None

    def test_pdf_senza_livello_testo(self, fake_corpus):
        from src.retrieval.page_title import extract_page_title
        assert extract_page_title("doc_scansione", 1) is None

    def test_pdf_assente_none_mai_eccezione(self, fake_corpus):
        from src.retrieval.page_title import extract_page_title
        assert extract_page_title("doc_inesistente", 1) is None

    def test_pagina_fuori_range_none(self, fake_corpus):
        from src.retrieval.page_title import extract_page_title
        assert extract_page_title("doc_con_testo", 999) is None
        assert extract_page_title("doc_con_testo", 0) is None

    def test_cache_un_solo_accesso_al_pdf(self, fake_corpus, monkeypatch):
        import src.retrieval.page_title as pt
        pt.clear_page_title_cache()
        calls = {"n": 0}
        real = pt.resolve_pdf

        def counting(stem):
            calls["n"] += 1
            return real(stem)
        monkeypatch.setattr(pt, "resolve_pdf", counting)
        pt.extract_page_title("doc_con_testo", 1)
        pt.extract_page_title("doc_con_testo", 1)
        pt.extract_page_title("doc_con_testo", 1)
        assert calls["n"] == 1          # il gate interroga le stesse pagine piu' volte

    def test_cache_anche_sui_fallimenti(self, fake_corpus, monkeypatch):
        import src.retrieval.page_title as pt
        pt.clear_page_title_cache()
        calls = {"n": 0}

        def counting(stem):
            calls["n"] += 1
            return None
        monkeypatch.setattr(pt, "resolve_pdf", counting)
        pt.extract_page_title("doc_inesistente", 1)
        pt.extract_page_title("doc_inesistente", 1)
        assert calls["n"] == 1


# ------------------------------------------------------- prompt e parsing

class TestPrompt:
    def test_lettere_chiuse_e_nessuna_esplicita(self):
        prompt = build_judge_prompt("esploso frizione", ["CLUTCH RELEASE", "FRONT AXLE"])
        assert "A)" in prompt and "B)" in prompt and "C)" not in prompt
        assert "NESSUNA" in prompt
        assert "esploso frizione" in prompt

    def test_pagina_senza_cartiglio_dichiarata_non_inventata(self):
        prompt = build_judge_prompt("q", ["CLUTCH RELEASE", None])
        assert "B)" in prompt
        assert "non disponibile" in prompt.lower()


class TestParseVerdict:
    @pytest.mark.parametrize("raw,expected", [
        ("A", 0), ("B", 1), ("C", 2),
        (" a ", 0), ("Risposta: B", 1), ("**C**", 2), ("C)", 2), ("(A)", 0),
    ])
    def test_lettere_valide(self, raw, expected):
        assert parse_judge_verdict(raw, 3) == expected

    @pytest.mark.parametrize("raw", [
        "NESSUNA", "nessuna", "Nessuna delle pagine risponde", "NONE",
    ])
    def test_nessuna(self, raw):
        assert parse_judge_verdict(raw, 3) is None

    @pytest.mark.parametrize("raw", [
        "D", "Z", "", "   ", "non lo so", "la pagina 69 e' quella giusta",
        "A e B", "forse la prima",
    ])
    def test_fuori_insieme_chiuso_e_nessuna(self, raw):
        # fail-closed sul parsing: tutto cio' che non e' una lettera ammessa
        # vale come NESSUNA (rifiuto), mai come conferma
        assert parse_judge_verdict(raw, 3) is None

    def test_lettera_oltre_i_candidati(self):
        # con 2 candidati, "C" non esiste -> NESSUNA
        assert parse_judge_verdict("C", 2) is None


# ------------------------------------------------------------- make_page_judge

class TestMakePageJudge:
    def test_conferma_pagina_top(self, fake_corpus):
        llm = FakeLLM("A")
        judge = make_page_judge(llm)
        out = judge("esploso frizione",
                    [PageHit(doc="doc_con_testo", page=1, score=0.9, n_tiles=1)])
        assert out.decision == "CONFIRM" and out.chosen_index == 0

    def test_nessuna_rifiuto(self, fake_corpus):
        judge = make_page_judge(FakeLLM("NESSUNA"))
        out = judge("il forno della cucina industriale",
                    [PageHit(doc="doc_con_testo", page=1, score=0.9, n_tiles=1)])
        assert out.decision == "REFUSE"

    def test_indica_pagina_diversa_conferma_ma_lo_registra(self, fake_corpus):
        judge = make_page_judge(FakeLLM("B"))
        pages = [PageHit(doc="doc_con_testo", page=1, score=0.9, n_tiles=1),
                 PageHit(doc="doc_con_testo", page=2, score=0.8, n_tiles=1)]
        out = judge("q", pages)
        # NON riordiniamo (cambiamento non misurato): confermiamo e registriamo
        assert out.decision == "CONFIRM" and out.chosen_index == 1

    def test_massimo_tre_candidati(self, fake_corpus):
        llm = FakeLLM("A")
        judge = make_page_judge(llm)
        pages = [PageHit(doc="doc_con_testo", page=1, score=0.9, n_tiles=1)] * 5
        judge("q", pages)
        assert "D)" not in llm.calls[0][1]

    def test_cartiglio_top_assente_salta_permissivo(self, fake_corpus):
        llm = FakeLLM("NESSUNA")
        judge = make_page_judge(llm)
        out = judge("q", [PageHit(doc="doc_scansione", page=1, score=0.9, n_tiles=1)])
        assert out.decision == "SKIP"
        assert llm.calls == []      # nemmeno interrogato: niente costo, niente rifiuto

    def test_llm_in_errore_salta_permissivo(self, fake_corpus):
        judge = make_page_judge(FakeLLM(error=RuntimeError("ollama giu'")))
        out = judge("q", [PageHit(doc="doc_con_testo", page=1, score=0.9, n_tiles=1)])
        assert out.decision == "SKIP" and "ollama" in out.reason

    def test_llm_assente_salta(self, fake_corpus):
        out = make_page_judge(None)(
            "q", [PageHit(doc="doc_con_testo", page=1, score=0.9, n_tiles=1)])
        assert out.decision == "SKIP"

    def test_nessuna_pagina_salta(self, fake_corpus):
        assert make_page_judge(FakeLLM("A"))("q", []).decision == "SKIP"


# ------------------------------------------------------- innesto nel grafo

class TestVisualNodeConGiudice:
    def test_giudice_rifiuta_bassa_nessuna_citazione(self):
        judge = lambda q, pages: JudgeOutcome("REFUSE", None, "nessuna pertinente")
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge))
        assert out["level"] == "BASSA"
        assert out["visual_pages"] == []
        assert out.get("answer") in (None,)
        assert any("giudice" in a for a in out["audit"])

    def test_motivo_nessuna_distinguibile_nell_audit(self):
        judge = lambda q, pages: JudgeOutcome("REFUSE", None, "nessuna pertinente")
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge))
        # i due motivi di rifiuto sono diagnostiche DIVERSE: devono restare
        # distinguibili a valle senza interpretare prosa
        assert any(REFUSE_NONE_TAG in a for a in out["audit"])
        assert not any(OTHER_PAGE_TAG in a for a in out["audit"])

    def test_giudice_conferma_media_invariata(self):
        judge = lambda q, pages: JudgeOutcome("CONFIRM", 0, "A")
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge))
        assert out["level"] == "MEDIA"
        assert out["visual_pages"] == _pages()
        assert out["citations"] == [("branson_4520R_parts", 69),
                                    ("branson_4520R_parts", 22)]

    def test_altra_pagina_NON_rifiuta_perche_e_gia_mostrata(self):
        # La pagina indicata dal giudice e' per costruzione fra le candidate che
        # gli abbiamo passato, cioe' fra quelle che l'utente vede: rifiutare li'
        # butterebbe via una risposta che CONTIENE la risposta. Il golden gate
        # l'ha misurato: V004/V011/V015 da PASS a MISS, zero guadagni.
        judge = lambda q, pages: JudgeOutcome("CONFIRM", 1, "B")
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge))
        assert out["level"] == "MEDIA"
        assert out["visual_pages"] == _pages()
        assert out["citations"] == [("branson_4520R_parts", 69),
                                    ("branson_4520R_parts", 22)]

    def test_altra_pagina_lascia_la_nota_di_audit(self):
        judge = lambda q, pages: JudgeOutcome("CONFIRM", 1, "B")
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge))
        # diagnostica conservata: quante volte il primo posto e' sbagliato pur
        # avendo la risposta in lista (il dato su cui si decidera' il riordino)
        assert any(OTHER_PAGE_TAG in a for a in out["audit"])
        assert any("p.22" in a for a in out["audit"])
        # ma NON e' un motivo di rifiuto
        assert not any(REFUSE_NONE_TAG in a for a in out["audit"])

    def test_nessun_riordino_la_pagina_indicata_non_va_in_cima(self):
        judge = lambda q, pages: JudgeOutcome("CONFIRM", 1, "B")
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge))
        # il giudice indica la seconda, ma l'ordine resta quello del retrieval
        assert out["citations"][0] == ("branson_4520R_parts", 69)

    def test_skip_non_blocca(self):
        judge = lambda q, pages: JudgeOutcome("SKIP", None, "cartiglio assente")
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=judge))
        assert out["level"] == "MEDIA"
        assert any("giudice" in a for a in out["audit"])

    def test_giudice_che_esplode_non_blocca(self):
        def boom(q, pages):
            raise RuntimeError("crash imprevisto del giudice")
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=boom))
        assert out["level"] == "MEDIA"          # un guasto non fabbrica rifiuti
        assert out["visual_pages"] == _pages()

    def test_giudice_assente_comportamento_identico_a_prima(self):
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, r: _pages(), judge=None))
        assert out["level"] == "MEDIA" and out["visual_pages"] == _pages()

    def test_giudice_non_interrogato_sotto_soglia_score(self):
        called = {"n": 0}

        def judge(q, pages):
            called["n"] += 1
            return JudgeOutcome("CONFIRM", 0, "A")
        weak = [PageHit(doc="d", page=1, score=0.10, n_tiles=1)]
        out = visual_node({"query": "esploso", "role": "tecnico"},
                          _deps(visual=lambda q, r: weak, floor=0.20, judge=judge))
        assert out["level"] == "BASSA" and called["n"] == 0

    def test_e2e_rifiuto_del_giudice_diventa_non_lo_so(self):
        judge = lambda q, pages: JudgeOutcome("REFUSE", None, "nessuna pertinente")
        res = answer("mostrami l'esploso del gruppo pompa", "tecnico",
                     _deps(visual=lambda q, r: _pages(), judge=judge))
        assert res["final_level"] == "BASSA"
        assert res["final_answer"] == REFUSAL

    def test_e2e_disaccordo_sul_quale_risponde_comunque(self):
        # la pagina indicata dal giudice e' fra quelle citate: l'utente la vede,
        # quindi la risposta resta (regola stretta ritirata dopo il golden gate)
        judge = lambda q, pages: JudgeOutcome("CONFIRM", 1, "B")
        res = answer("mostrami l'esploso del gruppo pompa", "tecnico",
                     _deps(visual=lambda q, r: _pages(), judge=judge))
        assert res["final_level"] == "MEDIA"
        cited = extract_citations(res["final_answer"])
        assert ("branson_4520R_parts", 22) in cited   # quella indicata dal giudice
        assert ("branson_4520R_parts", 69) in cited


# ------------------------------------------------------------- configurazione

class TestConfigurazione:
    def test_default_spento(self, monkeypatch):
        """Il default e' OFF perche' MISURATO in perdita sul metro del gate:
        0 confident-wrong eliminati, 2 risposte corrette perse (V011, CV09),
        +12.6% di tempo. Se qualcuno riporta il default a ON, deve prima
        rifare quella misura — vedi il docstring di `_page_judge_enabled`."""
        monkeypatch.delenv("AIKE_VISUAL_PAGE_JUDGE", raising=False)
        from src.serving.session_deps import _page_judge_enabled
        assert _page_judge_enabled() is False

    @pytest.mark.parametrize("val,expected", [
        ("0", False), ("false", False), ("FALSE", False),
        ("1", True), ("true", True), ("", False), ("boh", False),
    ])
    def test_env_spegne(self, monkeypatch, val, expected):
        monkeypatch.setenv("AIKE_VISUAL_PAGE_JUDGE", val)
        from src.serving.session_deps import _page_judge_enabled
        assert _page_judge_enabled() is expected

    def test_valore_effettivo_nel_blocco_config(self, monkeypatch):
        monkeypatch.setenv("AIKE_VISUAL_PAGE_JUDGE", "0")
        from src.common.run_config import effective_config, format_config_banner
        cfg = effective_config()
        assert cfg["visual_page_judge"]["enabled"] is False
        assert "giudice pagina" in format_config_banner(cfg)

    def test_config_osserva_le_deps_quando_fornite(self):
        from src.common.run_config import effective_config
        cfg = effective_config(_deps(visual=lambda q, r: [], judge=None))
        assert cfg["visual_page_judge"]["enabled"] is False
        assert cfg["visual_page_judge"]["source"] == "osservato"
