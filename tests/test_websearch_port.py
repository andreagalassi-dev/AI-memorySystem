"""Epica H — Port della ricerca online: adapter, Fake, cassetta offline.

NESSUN TEST QUI TOCCA LA RETE. L'adapter SearXNG e' provato contro un doppio
del client HTTP: e' proprio il motivo per cui il motore sta dietro un Port.

Il comportamento centrale sotto esame e' il FAIL-CLOSED: quando il servizio
non c'e', non risponde, o risponde storto, l'adapter SOLLEVA. Non restituisce
mai una lista vuota travestita da "il web non sa niente" — quella bugia
diventerebbe una risposta sbagliata in silenzio, che e' cio' che CLAUDE.md
vieta. La lista vuota resta lecita solo dove significa davvero "non c'e'
niente": il Fake e la cassetta.
"""

from __future__ import annotations

import json

import pytest

from src.retrieval.websearch import (
    DEFAULT_SEARXNG_URL,
    MAX_RESPONSE_BYTES,
    FakeWebSearch,
    FrozenWebSearch,
    SearxngWebSearch,
    WebResult,
    WebSearch,
    WebSearchUnavailable,
    build_web_search_from_env,
    record_cassette,
)

# Forma REALE di una risposta SearXNG (campi verificati dal vivo sul servizio
# del compose: title, url, content, engine, score).
RISPOSTA_REALE = {
    "query": "Rotax 912 oil pump",
    "number_of_results": 0,
    "results": [
        {"title": "Rotax 912 | 914 Ul Oil Pump Assembly",
         "url": "https://www.aircraftspruce.com/catalog/rtxpages/912914oilpump.php",
         "content": "Oil pump assembly for Rotax 912 and 914 engines.",
         "engine": "google cse", "score": 3.0},
        {"title": "Rotax Aircraft Engines - Lockwood Aero",
         "url": "https://lockwood.aero/shop/engines/ROTAX_912UL/5/42",
         "content": None, "engine": "google cse", "score": 1.0},
    ],
}


# ------------------------------------------------- doppio del client HTTP

class _FakeResponse:
    def __init__(self, status_code: int, body: bytes, chunk: int = 4096):
        self.status_code = status_code
        self._body = body
        self._chunk = chunk

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_bytes(self):
        for i in range(0, len(self._body), self._chunk):
            yield self._body[i:i + self._chunk]


class _FakeClient:
    """Doppio di `httpx.Client` limitato a cio' che l'adapter usa."""

    def __init__(self, response=None, error: Exception | None = None):
        self._response = response
        self._error = error
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    def stream(self, method, url, params=None):
        self.calls.append((url, dict(params or {})))
        if self._error is not None:
            raise self._error
        return self._response

    def close(self):
        self.closed = True


def _client_json(payload, status=200):
    return _FakeClient(_FakeResponse(status, json.dumps(payload).encode("utf-8")))


# ------------------------------------------------------------------ il Port

def test_i_tre_adapter_soddisfano_il_port():
    assert isinstance(FakeWebSearch(), WebSearch)
    assert isinstance(SearxngWebSearch(), WebSearch)


def test_fake_tre_modalita_come_fakellm():
    fisso = FakeWebSearch([WebResult("t", "https://a.test", "s")])
    assert [r.url for r in fisso.search("q")] == ["https://a.test"]
    assert fisso.calls == [("q", 5)]

    handler = FakeWebSearch(handler=lambda q, k: [WebResult(q, f"https://{q}.test", "")])
    assert handler.search("rotax")[0].url == "https://rotax.test"

    rotto = FakeWebSearch(error=WebSearchUnavailable("giu'"))
    with pytest.raises(WebSearchUnavailable):
        rotto.search("q")


def test_fake_rispetta_k():
    molti = [WebResult(f"t{i}", f"https://a.test/{i}", "") for i in range(10)]
    assert len(FakeWebSearch(molti).search("q", k=3)) == 3


# ------------------------------------------------------- adapter SearXNG

def test_searxng_legge_la_forma_reale_della_risposta():
    client = _client_json(RISPOSTA_REALE)
    res = SearxngWebSearch(client=client).search("Rotax 912 oil pump")

    assert len(res) == 2
    assert res[0].url == "https://www.aircraftspruce.com/catalog/rtxpages/912914oilpump.php"
    assert res[0].snippet.startswith("Oil pump assembly")
    assert res[0].engine == "google cse"
    assert res[0].score == 3.0
    # `content: null` non e' un errore: snippet vuoto, la fonte resta citabile.
    assert res[1].snippet == ""

    url, params = client.calls[0]
    assert url.endswith("/search")
    assert params["format"] == "json"      # senza questo SearXNG risponde 403
    assert params["q"] == "Rotax 912 oil pump"


def test_searxng_taglia_a_k_e_non_chiama_su_query_vuota():
    client = _client_json(RISPOSTA_REALE)
    assert len(SearxngWebSearch(client=client).search("q", k=1)) == 1
    assert SearxngWebSearch(client=client).search("   ") == []
    assert len(client.calls) == 1          # la query vuota non e' partita


def test_searxng_scarta_i_risultati_senza_url_utilizzabile():
    """Uno schema esotico in una 'fonte' non e' una fonte: fuori, in silenzio
    ma senza contaminare le altre."""
    payload = {"results": [
        {"title": "male", "url": "javascript:alert(1)", "content": "x"},
        {"title": "male", "url": "", "content": "x"},
        {"title": "male", "content": "senza url"},
        "non un dizionario",
        {"title": "buona", "url": "https://ok.test/1", "content": "y", "score": "nan-ish"},
    ]}
    res = SearxngWebSearch(client=_client_json(payload)).search("q")
    assert [r.url for r in res] == ["https://ok.test/1"]
    assert res[0].score == 0.0             # score non numerico -> 0.0, non un crash


@pytest.mark.parametrize("client, atteso", [
    (_FakeClient(error=OSError("connessione rifiutata")), "non raggiungibile"),
    (_client_json({"results": []}, status=403), "HTTP 403"),
    (_client_json({"results": []}, status=502), "HTTP 502"),
    (_FakeClient(_FakeResponse(200, b"<html>non json</html>")), "JSON non valido"),
    (_FakeClient(_FakeResponse(200, b'["lista"]')), "forma inattesa"),
    (_client_json({"nessun_campo_results": 1}), "senza campo 'results'"),
])
def test_searxng_fallisce_chiuso_e_non_mente(client, atteso):
    """SERVIZIO SPENTO = ERRORE ESPLICITO, mai '0 risultati'.

    E' la differenza fra "non lo so" e "sbagliato in silenzio": una lista vuota
    qui direbbe al sistema a valle che il web ha cercato e non ha trovato."""
    with pytest.raises(WebSearchUnavailable) as exc:
        SearxngWebSearch(client=client).search("q")
    assert atteso in str(exc.value)


def test_searxng_ferma_una_risposta_smisurata():
    """Tetto sui byte APPLICATO MENTRE SI LEGGE: la risposta enorme non entra
    mai tutta in memoria."""
    enorme = _FakeResponse(200, b"x" * (MAX_RESPONSE_BYTES + 1024), chunk=64 * 1024)
    with pytest.raises(WebSearchUnavailable, match="oltre il tetto"):
        SearxngWebSearch(client=_FakeClient(enorme)).search("q")


def test_searxng_default_punta_al_servizio_del_compose():
    assert SearxngWebSearch().base_url == DEFAULT_SEARXNG_URL == "http://localhost:8888"
    assert SearxngWebSearch("http://searxng:8080/").base_url == "http://searxng:8080"


# ------------------------------------------------------ cassetta offline

CASSETTA = {
    "version": 1,
    "entries": {
        "Rotax 912 oil pump": [
            {"title": "Aircraft Spruce", "url": "https://www.aircraftspruce.com/x",
             "snippet": "Oil pump assembly.", "engine": "google", "score": 3.0},
            {"title": "senza url valido", "url": "javascript:alert(1)", "snippet": "no"},
        ],
        "NordCap Kühltruhe Ersatzteile": [
            {"title": "NordCap", "url": "https://nordcap.de/ersatzteile",
             "content": "Ersatzteile für Kühltruhen.", "engine": "ddg"},
        ],
    },
}


@pytest.fixture()
def cassetta(tmp_path):
    p = tmp_path / "web_cassette.json"
    p.write_text(json.dumps(CASSETTA, ensure_ascii=False), encoding="utf-8")
    return p


def test_cassetta_rilegge_senza_rete(cassetta):
    """La demo non deve dipendere dal wifi della sala."""
    frozen = FrozenWebSearch(cassetta)
    res = frozen.search("Rotax 912 oil pump")
    assert [r.url for r in res] == ["https://www.aircraftspruce.com/x"]  # il velenoso e' fuori
    assert res[0].score == 3.0
    # `content` e' accettato come sinonimo di `snippet` (forma nativa SearXNG).
    assert frozen.search("NordCap Kühltruhe Ersatzteile")[0].snippet.startswith("Ersatzteile")


def test_cassetta_normalizza_la_chiave_ma_non_confronta_per_sottostringa(cassetta):
    frozen = FrozenWebSearch(cassetta)
    assert frozen.search("  rotax 912   OIL   pump ")      # spazi e maiuscole: stessa domanda
    assert frozen.search("Rotax 912") == []                # prefisso: NON e' la stessa domanda
    assert frozen.search("Rotax 912 oil pump filter") == []


def test_cassetta_assente_o_illeggibile_fallisce_chiuso(tmp_path):
    with pytest.raises(WebSearchUnavailable, match="assente"):
        FrozenWebSearch(tmp_path / "manca.json")
    rotta = tmp_path / "rotta.json"
    rotta.write_text("{non json", encoding="utf-8")
    with pytest.raises(WebSearchUnavailable, match="illeggibile"):
        FrozenWebSearch(rotta)
    strana = tmp_path / "strana.json"
    strana.write_text('{"version": 1}', encoding="utf-8")
    with pytest.raises(WebSearchUnavailable, match="forma inattesa"):
        FrozenWebSearch(strana)


def test_registrazione_e_rilettura_sono_lo_stesso_risultato(tmp_path):
    """Round-trip: cio' che un motore restituisce e cio' che la cassetta
    rilegge devono coincidere, altrimenti la demo mostra un'altra cosa."""
    vero = FakeWebSearch([
        WebResult("Aircraft Spruce", "https://www.aircraftspruce.com/x", "Oil pump.",
                  "google", 3.0),
        WebResult("Lockwood", "https://lockwood.aero/shop", "", "ddg", 1.0),
    ])
    path = record_cassette(vero, ["Rotax 912 oil pump"], tmp_path / "c.json")
    riletti = FrozenWebSearch(path).search("Rotax 912 oil pump")
    assert riletti == vero.search("Rotax 912 oil pump")


# ------------------------------------------------------------------ factory

def test_air_gapped_il_ramo_web_semplicemente_non_esiste():
    """Nessuna variabile impostata = nessun motore = sistema identico a prima.

    E' il requisito di installazione air-gapped: non un ramo web che fallisce,
    proprio un ramo web che non c'e'."""
    assert build_web_search_from_env({}) is None
    assert build_web_search_from_env({"AIKE_WEB_SEARCH": "off"}) is None
    assert build_web_search_from_env({"AIKE_WEB_SEARCH": "false"}) is None
    assert build_web_search_from_env({"AIKE_WEB_SEARCH": "pippo"}) is None
    # frozen senza cassetta: si spegne, NON esplode a runtime piu' tardi.
    assert build_web_search_from_env({"AIKE_WEB_SEARCH": "frozen"}) is None


def test_factory_costruisce_il_motore_richiesto(cassetta):
    m = build_web_search_from_env({"AIKE_WEB_SEARCH": "searxng",
                                   "SEARXNG_URL": "http://searxng:8080"})
    assert isinstance(m, SearxngWebSearch) and m.base_url == "http://searxng:8080"

    assert isinstance(build_web_search_from_env({"AIKE_WEB_SEARCH": "searxng"}),
                      SearxngWebSearch)

    f = build_web_search_from_env({"AIKE_WEB_SEARCH": "frozen",
                                   "AIKE_WEB_CASSETTE": str(cassetta)})
    assert isinstance(f, FrozenWebSearch)
    assert f.search("Rotax 912 oil pump")
