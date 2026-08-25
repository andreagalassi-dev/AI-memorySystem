"""Il punteggio visivo PRIMA della soglia: registrarlo senza cambiare nulla.

Il difetto che questi test presidiano non e' un bug di calcolo: e' che
`VISUAL_SCORE_FLOOR = 0.20` e' stata tarata a intuito, e **il numero su cui si
decide non viene mai registrato**. Quando la soglia scarta, `visual_pages`
diventa `[]` e lo score che ha causato lo scarto sparisce: chiunque voglia
ritarare la soglia deve ri-misurare da zero, o tirare a indovinare un'altra
volta.

Due proprieta', e sono in tensione:
  1. con la registrazione SPENTA (default) il nodo si comporta ESATTAMENTE come
     prima — stessi dizionari di ritorno, non "una forma simile";
  2. con la registrazione ACCESA lo score pre-soglia c'e' anche — soprattutto —
     nel caso in cui la soglia SCARTA.

I finti non asseriscono sulla forma: `visual_node` viene eseguito davvero e si
confrontano i RISULTATI, che e' la disciplina di `tests/test_delete_document_points.py`.
"""

from __future__ import annotations

import json

import pytest

from src.graph import visual_trace
from src.graph.pipeline import Deps, visual_node
from src.retrieval.confidence import Thresholds
from src.retrieval.pixelrag.index import PageHit

_T = Thresholds(tau_hi=0.5, tau_lo=0.2)

FORTI = [PageHit(doc="esploso_pompa", page=7, score=0.62, n_tiles=3),
         PageHit(doc="esploso_pompa", page=8, score=0.41, n_tiles=1),
         PageHit(doc="altro_doc", page=3, score=0.11, n_tiles=1)]

DEBOLI = [PageHit(doc="copertina", page=1, score=0.13, n_tiles=2),
          PageHit(doc="copertina", page=2, score=0.09, n_tiles=1)]


def _deps(pages, *, floor=None):
    return Deps(
        retrieve=lambda q: ([], []),
        synthesize=lambda q, ev: "n/a",
        catalog_lookup=lambda k: False,
        thresholds=_T,
        visual_search=lambda q, role: list(pages),
        visual_score_floor=floor,
    )


def _stato(query="mostrami l'esploso del gruppo pompa"):
    return {"query": query, "role": "tecnico"}


@pytest.fixture(autouse=True)
def _pulizia(monkeypatch):
    """Ogni test parte da registrazione SPENTA e buffer vuoto."""
    monkeypatch.delenv(visual_trace.TRACE_ENV, raising=False)
    monkeypatch.delenv(visual_trace.TRACE_PATH_ENV, raising=False)
    visual_trace.reset()
    yield
    visual_trace.reset()


def _accendi(monkeypatch, path=None):
    monkeypatch.setenv(visual_trace.TRACE_ENV, "1")
    if path is not None:
        monkeypatch.setenv(visual_trace.TRACE_PATH_ENV, str(path))


# ------------------------------------------------ 1. spenta = identico a prima

class TestRegistrazioneSpenta:
    """Confronto di RISULTATI, non di forma: gli stessi input, gli stessi output."""

    @pytest.mark.parametrize("pages,floor", [
        (FORTI, None),          # reranker spento: nessun gate
        (FORTI, 0.20),          # passa la soglia
        (DEBOLI, 0.20),         # scartata dalla soglia
        ([], 0.20),             # nessuna pagina
        ([], None),
    ])
    def test_output_identico_al_comportamento_storico(self, pages, floor):
        atteso = _riferimento(pages, floor)
        out = visual_node(_stato(), _deps(pages, floor=floor))
        assert out == atteso

    def test_nessun_record_prodotto(self):
        visual_node(_stato(), _deps(DEBOLI, floor=0.20))
        visual_node(_stato(), _deps(FORTI, floor=0.20))
        assert visual_trace.take() == []

    def test_nessun_file_scritto_anche_col_path_impostato(self, monkeypatch, tmp_path):
        """Il path da solo non accende nulla: l'interruttore e' uno solo."""
        dest = tmp_path / "trace.jsonl"
        monkeypatch.setenv(visual_trace.TRACE_PATH_ENV, str(dest))
        visual_node(_stato(), _deps(DEBOLI, floor=0.20))
        assert not dest.exists()


def _riferimento(pages, floor):
    """Il comportamento STORICO del nodo, riscritto qui a mano.

    Volutamente duplicato invece che importato: se qualcuno cambia il nodo,
    questo test deve rompersi, non seguirlo."""
    from src.graph.tools.visual_tool import format_visual_answer, visual_citations

    if not pages:
        return {"visual_pages": [], "level": "BASSA",
                "audit": ["visual: 0 pagine (o filtrate da RBAC) -> non lo so"]}
    if floor is not None and pages[0].score < floor:
        return {"visual_pages": [], "level": "BASSA",
                "audit": [f"visual: score top {pages[0].score:.3f} < soglia "
                          f"{floor:.2f} -> immagine non chiaramente pertinente "
                          f"(non lo so)"]}
    return {"visual_pages": list(pages), "answer": format_visual_answer(pages),
            "citations": visual_citations(pages), "level": "MEDIA",
            "audit": [f"visual: {len(pages)} pagina/e -> MEDIA"]}


# --------------------------------- 2. accesa = lo score sopravvive allo scarto

class TestRegistrazioneAccesa:

    def test_lo_score_pre_soglia_sopravvive_allo_scarto(self, monkeypatch):
        """IL caso che oggi perde l'informazione."""
        _accendi(monkeypatch)
        out = visual_node(_stato(), _deps(DEBOLI, floor=0.20))
        assert out["visual_pages"] == [], "la decisione NON deve cambiare"

        (rec,) = visual_trace.take()
        assert rec.outcome == visual_trace.OUTCOME_REJECTED
        assert rec.top_score == pytest.approx(0.13)
        assert rec.score_floor == 0.20
        # non solo la prima: tutte le K pagine, con doc e pagina
        assert rec.pages == (("copertina", 1, pytest.approx(0.13)),
                             ("copertina", 2, pytest.approx(0.09)))

    def test_registra_anche_quando_la_soglia_passa(self, monkeypatch):
        _accendi(monkeypatch)
        out = visual_node(_stato(), _deps(FORTI, floor=0.20))
        assert out["level"] == "MEDIA"
        (rec,) = visual_trace.take()
        assert rec.outcome == visual_trace.OUTCOME_PASSED
        assert rec.top_score == pytest.approx(0.62)
        assert [p[0:2] for p in rec.pages] == [("esploso_pompa", 7),
                                               ("esploso_pompa", 8),
                                               ("altro_doc", 3)]

    def test_registra_lo_zero_pagine(self, monkeypatch):
        _accendi(monkeypatch)
        visual_node(_stato(), _deps([], floor=0.20))
        (rec,) = visual_trace.take()
        assert rec.outcome == visual_trace.OUTCOME_NO_PAGES
        assert rec.top_score is None and rec.pages == ()

    def test_reranker_spento_soglia_assente_e_dichiarata(self, monkeypatch):
        """floor=None e' l'impronta del reranker spento (vedi session_deps)."""
        _accendi(monkeypatch)
        visual_node(_stato(), _deps(DEBOLI, floor=None))
        (rec,) = visual_trace.take()
        assert rec.score_floor is None
        assert rec.outcome == visual_trace.OUTCOME_PASSED
        assert rec.floor_gate_armed is False

    def test_la_decisione_non_cambia_mai(self, monkeypatch):
        """Stessi input, registrazione ON vs OFF: stesso identico output."""
        for pages, floor in ((FORTI, 0.20), (DEBOLI, 0.20), ([], 0.20), (FORTI, None)):
            spento = visual_node(_stato(), _deps(pages, floor=floor))
            _accendi(monkeypatch)
            acceso = visual_node(_stato(), _deps(pages, floor=floor))
            monkeypatch.delenv(visual_trace.TRACE_ENV, raising=False)
            assert acceso == spento

    def test_take_svuota_il_buffer(self, monkeypatch):
        _accendi(monkeypatch)
        visual_node(_stato(), _deps(FORTI, floor=0.20))
        assert len(visual_trace.take()) == 1
        assert visual_trace.take() == []

    def test_riga_jsonl_su_file(self, monkeypatch, tmp_path):
        dest = tmp_path / "sotto" / "trace.jsonl"
        _accendi(monkeypatch, dest)
        visual_node(_stato("esploso pompa"), _deps(DEBOLI, floor=0.20))
        righe = [json.loads(r) for r in dest.read_text(encoding="utf-8").splitlines()]
        assert len(righe) == 1
        r = righe[0]
        assert r["query"] == "esploso pompa"
        assert r["role"] == "tecnico"
        assert r["outcome"] == visual_trace.OUTCOME_REJECTED
        assert r["top_score"] == pytest.approx(0.13)
        assert r["pages"][0] == {"doc": "copertina", "page": 1, "score": 0.13}

    def test_un_osservatore_rotto_non_cambia_la_risposta(self, monkeypatch):
        """Se il sink esplode, il nodo deve rispondere come se nulla fosse."""
        _accendi(monkeypatch)

        def _esplode(*a, **k):
            raise RuntimeError("disco pieno")

        monkeypatch.setattr(visual_trace, "_append_jsonl", _esplode)
        monkeypatch.setenv(visual_trace.TRACE_PATH_ENV, "/dev/null/impossibile")
        out = visual_node(_stato(), _deps(FORTI, floor=0.20))
        assert out == _riferimento(FORTI, 0.20)


# ------------------------------------------- 3. il modulo in isolamento

class TestModulo:

    def test_buffer_limitato(self, monkeypatch):
        _accendi(monkeypatch)
        for i in range(visual_trace.MAX_RECORDS + 25):
            visual_trace.record(query=f"q{i}", role="tecnico", pages=FORTI[:1],
                                floor=0.20, outcome=visual_trace.OUTCOME_PASSED)
        recs = visual_trace.take()
        assert len(recs) == visual_trace.MAX_RECORDS
        assert recs[-1].query == f"q{visual_trace.MAX_RECORDS + 24}"

    @pytest.mark.parametrize("valore,atteso", [("1", True), ("true", True),
                                               ("TRUE", True), ("0", False),
                                               ("", False), ("boh", False)])
    def test_interruttore(self, monkeypatch, valore, atteso):
        monkeypatch.setenv(visual_trace.TRACE_ENV, valore)
        assert visual_trace.trace_enabled() is atteso

    def test_spento_di_default(self):
        assert visual_trace.trace_enabled() is False
