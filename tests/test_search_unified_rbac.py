"""`search_unified` promette RBAC fail-closed (`role` obbligatorio, keyword-only)
ma il ruolo arrivava SOLO all'Hop 2 (`apply_hop2`): la ricerca PRINCIPALE
(`search(client, ..., "routed", limit)` a riga ~223, e il canale lessicale sul
`code_token` nel ramo visivo a riga ~217) girava senza `query_filter`. Misurato
sull'indice vivo: 196876 punti su 199689 (98.6%) di `aike_chunks_v2` erano
raggiungibili da QUALSIASI ruolo attraverso questa funzione.

E' il duale esatto della "famiglia di guardie scritte, testate e mai cablate
nel grafo" gia' trovata in questo progetto: qui il parametro di sicurezza
c'e' nella firma, ma non era usato dove conta.

Il test NON si limita a guardare `text_hits` finali (l'Hop 2 applica il suo
proprio filtro-ruolo sui FIGLI e potrebbe mascherare il difetto): sostituisce
`search()` con una spia che APPLICA DAVVERO il filtro ricevuto a un corpus
multi-ruolo/multi-versione, cosi' misura sia il comportamento finale sia
l'argomento `query_filter` che arriva alla chiamata PRINCIPALE. Stessa
disciplina di `tests/test_delete_document_points.py` e
`tests/test_graph_hop2_rbac.py`.
"""

from __future__ import annotations

import pytest
from qdrant_client import models

import src.retrieval.search as S
from src.common.roles import Role

# Corpus multi-ruolo E multi-versione: serve a dimostrare sia il leak di ruolo
# sia la perdita di version_status (un filtro-solo-ruolo farebbe rientrare
# CCC333, che e' 'superseded').
CORPUS = [
    {"id": 1, "code": "AAA111", "ruoli_consentiti": ["tecnico"], "version_status": "current"},
    {"id": 2, "code": "BBB222", "ruoli_consentiti": ["commerciale"], "version_status": "current"},
    {"id": 3, "code": "CCC333", "ruoli_consentiti": ["tecnico", "commerciale"], "version_status": "superseded"},
]


def _match(payload: dict, cond: models.FieldCondition) -> bool:
    """Semantica Qdrant usata qui: MatchValue su lista = 'la lista contiene'."""
    got = payload.get(cond.key)
    want = cond.match.value
    return want in got if isinstance(got, list) else got == want


def _applica(flt: models.Filter | None, corpus: list[dict]) -> list[dict]:
    if flt is None:
        # questo E' il comportamento pre-fix: nessun filtro -> tutto passa.
        return list(corpus)
    conds = list(flt.must or [])
    return [p for p in corpus if all(_match(p, c) for c in conds)]


class SearchSpy:
    """Sostituisce `src.retrieval.search.search`: applica DAVVERO il filtro
    ricevuto (non ne guarda solo la forma) e registra ogni chiamata, cosi' il
    test puo' verificare sia il risultato sia CHE il filtro sia arrivato."""

    def __init__(self, corpus: list[dict]):
        self.corpus = corpus
        self.calls: list[tuple[str, models.Filter | None]] = []

    def __call__(self, client, query, strategy, limit=10, query_filter=None,
                extra_terms=None):
        self.calls.append((strategy, query_filter))
        matched = _applica(query_filter, self.corpus)
        return [models.ScoredPoint(id=p["id"], version=0, score=1.0,
                                   payload=p, vector=None) for p in matched]


class FakeQdrant:
    """Hop 2 (`apply_hop2`) legge via `.scroll`: nessun punto del corpus ha
    `is_assembly`, quindi non viene mai invocato — presente solo per non far
    esplodere una chiamata imprevista."""

    def scroll(self, collection, scroll_filter=None, limit=100, **kw):
        return [], None


@pytest.fixture
def spy(monkeypatch):
    s = SearchSpy(CORPUS)
    monkeypatch.setattr(S, "search", s)
    return s


@pytest.fixture
def no_visual_model(monkeypatch):
    """Il ramo visivo di `search_unified` costruisce Qwen3VLEmbedder/Reranker
    reali: qui e' fuori perimetro (VINCOLO ASSOLUTO — vietato caricare
    modelli), quindi si sostituisce `visual_search` con un fake inerte PRIMA
    che `search_unified` lo importi (import locale, risolto a ogni chiamata:
    patchare l'attributo del modulo sorgente basta)."""
    import src.retrieval.pixelrag.search as VS
    monkeypatch.setattr(VS, "visual_search", lambda *a, **kw: [])


class TestRamoRouted:
    """Riga ~223: `search(client, query, "routed", limit)` — query
    natural/mixed/code non-visive. E' il call-site che mancava del tutto di
    `query_filter` prima della correzione."""

    def test_ruolo_autorizzato_trova_i_suoi_punti(self, spy):
        out = S.search_unified(FakeQdrant(), "guarnizione pompa", role=Role.TECNICO)
        codici = {h.payload["code"] for h in out["text_hits"]}
        assert "AAA111" in codici, "il tecnico deve continuare a vedere i suoi punti"

    def test_ruolo_non_autorizzato_non_trova_i_punti_altrui(self, spy):
        out = S.search_unified(FakeQdrant(), "guarnizione pompa", role=Role.COMMERCIALE)
        codici = {h.payload["code"] for h in out["text_hits"]}
        assert "AAA111" not in codici, (
            "leak RBAC: il commerciale ha ricevuto un punto riservato al "
            "tecnico dalla ricerca PRINCIPALE (non dall'Hop 2)")

    def test_ramo_code_token_singolo(self, spy):
        """route 'code' (token singolo con cifre) passa per la STESSA riga 223."""
        out = S.search_unified(FakeQdrant(), "AAA111", role=Role.COMMERCIALE)
        codici = {h.payload["code"] for h in out["text_hits"]}
        assert "AAA111" not in codici

    def test_il_filtro_arriva_alla_ricerca_principale(self, spy):
        """Guarda l'ARGOMENTO passato a search(), non solo l'output finale:
        un test che guarda solo `text_hits` potrebbe passare per via
        dell'Hop 2, che il suo filtro-ruolo ce l'ha gia' (e non e' quello sotto
        esame qui)."""
        S.search_unified(FakeQdrant(), "guarnizione pompa", role=Role.TECNICO)
        assert spy.calls, "search_unified non ha nemmeno chiamato search()"
        strategy, flt = spy.calls[-1]
        assert strategy == "routed"
        assert flt is not None, (
            "query_filter=None sulla ricerca PRINCIPALE: il ruolo non e' "
            "cablato li', solo l'Hop 2 lo applica (il difetto documentato)")

    def test_version_status_non_si_perde(self, spy):
        S.search_unified(FakeQdrant(), "guarnizione pompa", role=Role.TECNICO)
        _, flt = spy.calls[-1]
        chiavi = {c.key: c.match.value for c in (flt.must or [])}
        assert chiavi.get("version_status") == "current", (
            "il filtro applica il ruolo ma perde version_status: farebbe "
            "rientrare documenti superseded/deleted nella ricerca principale")


class TestRamoVisualeCodeToken:
    """Riga ~217: query con marker visivo + code_token -> il canale lessicale
    sul codice (`search(client, route.code_token, "sparse", limit)`) e' il
    SECONDO call-site che mancava di `query_filter`."""

    def test_ruolo_autorizzato_trova_il_codice(self, spy, no_visual_model):
        out = S.search_unified(FakeQdrant(), "esploso codice 11282", role=Role.TECNICO)
        codici = {h.payload["code"] for h in out["text_hits"]}
        assert "AAA111" in codici

    def test_ruolo_non_autorizzato_non_trova_il_codice(self, spy, no_visual_model):
        out = S.search_unified(FakeQdrant(), "esploso codice 11282", role=Role.COMMERCIALE)
        codici = {h.payload["code"] for h in out["text_hits"]}
        assert "AAA111" not in codici, (
            "leak RBAC nel ramo visivo: il canale lessicale sul code_token "
            "non e' filtrato per ruolo")

    def test_filtro_arriva_al_canale_lessicale(self, spy, no_visual_model):
        S.search_unified(FakeQdrant(), "esploso codice 11282", role=Role.TECNICO)
        assert spy.calls, "il canale lessicale sul code_token non e' stato chiamato"
        strategy, flt = spy.calls[-1]
        assert strategy == "sparse"
        assert flt is not None, (
            "query_filter=None sul canale lessicale visivo: il ruolo non e' "
            "cablato li'")


class TestVersionStatusParametro:
    """`search_unified` non conosce di per se' il version_status del
    chiamante: e' un parametro esplicito con default 'current' (lo scope piu'
    restrittivo, coerente con `visual_search` in pixelrag)."""

    def test_default_e_current(self, spy):
        S.search_unified(FakeQdrant(), "guarnizione pompa", role=Role.TECNICO)
        _, flt = spy.calls[-1]
        chiavi = {c.key: c.match.value for c in (flt.must or [])}
        assert chiavi.get("version_status") == "current"

    def test_version_status_esplicito_viene_rispettato(self, spy):
        S.search_unified(FakeQdrant(), "guarnizione pompa", role=Role.TECNICO,
                         version_status="superseded")
        _, flt = spy.calls[-1]
        chiavi = {c.key: c.match.value for c in (flt.must or [])}
        assert chiavi.get("version_status") == "superseded"
