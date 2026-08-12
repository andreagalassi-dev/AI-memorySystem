from unittest.mock import MagicMock
from qdrant_client import models
from src.retrieval.qwen3vl import doc_hint_filter
from src.retrieval.pixelrag.index import QdrantVisualIndex

def test_v009_doc_hint_filter(monkeypatch):
    # Fake cache to contain KBS documenti
    monkeypatch.setattr("src.retrieval.qwen3vl._indexed_docs_cache", lambda: ("kbs_gastrotechnik_1717", "other_doc"))
    
    query = "esploso del forno KBS gastronomia"
    flt = doc_hint_filter(query)
    
    assert flt is not None
    # 'kbs' has 3 letters, so it should be matched
    matched_docs = flt.must[0].match.any
    assert "kbs_gastrotechnik_1717" in matched_docs
    assert "other_doc" not in matched_docs


def _cache(monkeypatch, *docs):
    monkeypatch.setattr("src.retrieval.qwen3vl._indexed_docs_cache", lambda: tuple(docs))


def test_hint_non_matcha_dentro_le_parole_dello_stem(monkeypatch):
    """V008: 'der' non e' un hint — non deve pescare schneiDER/siDERurgico/crusaDER.

    Il match era per SOTTOSTRINGA sullo stem intero, quindi una particella
    grammaticale tedesca di 3 lettere selezionava tre cataloghi che non c'entrano
    nulla, e la ricerca partiva ristretta a quelli. Il token deve corrispondere a
    una PARTE INTERA del nome-documento, non a un pezzo di parola.
    """
    _cache(monkeypatch, "NordCap_Esploso_Ricambi_DE", "as_schneider_ball_valves_2026",
           "centro_siderurgico_listino_tubi_dic2025", "crusader_marine_L510008")

    matched = doc_hint_filter("Explosionszeichnung der NordCap Kuhltruhe").must[0].match.any

    assert matched == ["NordCap_Esploso_Ricambi_DE"]


def test_hint_non_esclude_il_documento_giusto_per_punteggiatura(monkeypatch):
    """CV07: i token veri erano scartati da `isalpha()` per colpa delle parentesi.

    '(KBS' e 'Gastrotechnik)' non sono `isalpha()`, quindi venivano buttati: restava
    solo 'der', e il filtro risultante ESCLUDEVA il documento atteso. E' il difetto
    peggiore possibile in un fail-closed — il sistema non puo' recuperare da un
    candidato che non ha mai visto. La punteggiatura va NORMALIZZATA, non usata come
    criterio di scarto.
    """
    _cache(monkeypatch, "kbs_gastrotechnik_4055041", "crusader_marine_L510008",
           "as_schneider_ball_valves_2026")

    matched = doc_hint_filter(
        "Explosionszeichnung der Schraube MSX18-T25 (KBS Gastrotechnik)").must[0].match.any

    assert "kbs_gastrotechnik_4055041" in matched
    assert "crusader_marine_L510008" not in matched


def test_hint_fail_open_se_nessun_documento_matcha(monkeypatch):
    """Nessun token riconosciuto -> nessun filtro (ricerca globale), MAI filtro vuoto.

    Fail-OPEN e' la direzione giusta qui: il filtro e' un acceleratore di precisione,
    non un controllo d'accesso (l'RBAC e' un filtro separato, quello sí fail-closed).
    """
    _cache(monkeypatch, "kbs_gastrotechnik_4055041", "rotax_912iS_IPC")

    assert doc_hint_filter("mostrami un disegno tecnico qualsiasi") is None


def test_hint_matcha_parti_numeriche_del_nome(monkeypatch):
    """'BL200' e' un token-documento legittimo: `isalpha()` lo scartava."""
    _cache(monkeypatch, "panciroli_garmec_BL200", "rotax_912iS_IPC")

    matched = doc_hint_filter("despiece de la BL200").must[0].match.any

    assert matched == ["panciroli_garmec_BL200"]


class DummyPoint:
    def __init__(self, doc, page):
        self.id = f"{doc}-{page}"
        self.score = 0.9
        self.payload = {"doc": doc, "page": page}


def test_search_visual_non_scarta_il_risultato_filtrato(monkeypatch):
    """Un risultato filtrato valido NON si butta, nemmeno se ha poche pagine.

    Aspettativa RIVISTA (12/08). Prima questo test asseriva l'opposto: che con meno
    di 10 pagine distinte l'adapter rifacesse la ricerca SENZA hint e sostituisse il
    risultato filtrato con quello globale. Quell'aspettativa e' sbagliata: la soglia
    confondeva "poche pagine distinte" con "filtro sbagliato", mentre su un
    documento piccolo (NordCap: 2 pagine indicizzate) poche pagine distinte sono
    l'esito CORRETTO del filtro.

    Misurato: lo scarto costa 3 query su 34 (V008, CV06, CV15) non appena
    `doc_hint_filter` diventa preciso — piu' il filtro e' buono, piu' spesso lo
    scarto lo distrugge. Su V008 il filtro isola esattamente il documento giusto
    (NordCap) e lo scarto lo butta via.

    Non si completa con risultati globali: `k` qui e' il pool di TILE per il
    reranker (300) e riempirlo di tile estranei diluisce il pool — misurato,
    costa piu' di quanto renda (golden R@3 0.667 -> 0.467).
    """
    dummy_filter = models.Filter(must=[models.FieldCondition(
        key="doc", match=models.MatchAny(any=["NordCap_Esploso_Ricambi_DE"]))])
    monkeypatch.setattr("src.retrieval.qwen3vl.doc_hint_filter", lambda q: dummy_filter)

    client = MagicMock()
    index = QdrantVisualIndex(client)
    filtrati = MagicMock()
    filtrati.points = [DummyPoint("NordCap_Esploso_Ricambi_DE", 1),
                       DummyPoint("NordCap_Esploso_Ricambi_DE", 2)]
    client.query_points.side_effect = [filtrati, MagicMock()]

    hits = index.search([0.1, 0.2], k=300,
                        query_text="Explosionszeichnung der NordCap Kuhltruhe")

    assert client.query_points.call_count == 1, "nessuna seconda ricerca globale"
    assert [h.doc for h in hits] == ["NordCap_Esploso_Ricambi_DE"] * 2


def test_search_visual_senza_hint_invariato(monkeypatch):
    """Senza hint_filter: un solo round-trip col solo filtro RBAC, come sempre."""
    monkeypatch.setattr("src.retrieval.qwen3vl.doc_hint_filter", lambda q: None)
    client = MagicMock()
    index = QdrantVisualIndex(client)
    res = MagicMock()
    res.points = [DummyPoint("a", 1)]
    client.query_points.side_effect = [res]

    hits = index.search([0.1, 0.2], k=300, query_text="query senza nomi documento")

    assert client.query_points.call_count == 1
    _, kwargs = client.query_points.call_args_list[0]
    assert kwargs.get("query_filter") is None
    assert len(hits) == 1
