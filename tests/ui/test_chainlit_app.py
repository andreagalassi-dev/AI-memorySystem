"""Test dell'interfaccia (Epica H): provenienza corpus/web nella UI.

Copre le funzioni pure di src/ui/chainlit_app.py — classificazione delle
citazioni, resa del livello di confidenza (tollerante a un valore nuovo) e
riepilogo di provenienza — senza avviare Chainlit. Nessuna di queste tocca
il grafo: verificano solo come l'interfaccia LEGGE cio' che answer()
restituisce oggi (citazioni corpus = (doc, page)) e come si comporterebbe
se arrivasse una citazione web (URL / mapping con 'url') o un final_level
mai visto prima.
"""

from src.ui.chainlit_app import (
    LEVEL_EMOJI,
    WEB_WARNING,
    classify_citations,
    provenance_summary,
    render_level,
    web_citation_link,
)


class TestClassifyCitations:
    def test_solo_corpus(self):
        citations = [("doc1", 10), ("doc2", 3)]
        corpus, web = classify_citations(citations)
        assert corpus == citations
        assert web == []

    def test_solo_web_stringhe(self):
        citations = ["https://example.com/a", "https://example.com/b"]
        corpus, web = classify_citations(citations)
        assert corpus == []
        assert web == citations

    def test_web_come_mapping(self):
        citations = [{"url": "https://example.com/a", "title": "Pagina A"}]
        corpus, web = classify_citations(citations)
        assert corpus == []
        assert web == citations

    def test_misto_preserva_ordine(self):
        citations = [("doc1", 1), "https://example.com/a", ("doc2", 2)]
        corpus, web = classify_citations(citations)
        assert corpus == [("doc1", 1), ("doc2", 2)]
        assert web == ["https://example.com/a"]

    def test_none_non_rompe(self):
        assert classify_citations(None) == ([], [])

    def test_lista_vuota(self):
        assert classify_citations([]) == ([], [])

    def test_mapping_senza_url_e_corpus_per_non_rompere_nulla(self):
        # Un mapping senza 'url' non è una fonte web riconoscibile: cade nel
        # ramo corpus (comportamento di oggi), non sparisce.
        citations = [{"doc": "doc1", "page": 1}]
        corpus, web = classify_citations(citations)
        assert corpus == citations
        assert web == []


class TestRenderLevel:
    def test_livelli_noti_invariati(self):
        for level, label in LEVEL_EMOJI.items():
            assert render_level(level) == label

    def test_livello_web_ha_etichetta_dedicata(self):
        # Level.WEB.value == "WEB" (src/retrieval/confidence.py, epica H):
        # non deve cadere nel fallback generico, ha una voce sua.
        assert render_level("WEB") == LEVEL_EMOJI["WEB"]
        assert "WEB" in render_level("WEB")

    def test_livello_sconosciuto_non_e_vuoto(self):
        out = render_level("WEB_NUOVO_MAI_VISTO")
        assert out
        assert "WEB_NUOVO_MAI_VISTO" in out

    def test_livello_none_non_e_vuoto(self):
        assert render_level(None)

    def test_livello_stringa_vuota_non_e_vuoto(self):
        assert render_level("")


class TestWebCitationLink:
    def test_stringa_url(self):
        assert web_citation_link("https://example.com/x") == (
            "https://example.com/x",
            "https://example.com/x",
        )

    def test_mapping_con_titolo(self):
        c = {"url": "https://example.com/x", "title": "Fonte X"}
        assert web_citation_link(c) == ("Fonte X", "https://example.com/x")

    def test_mapping_senza_titolo_usa_url(self):
        c = {"url": "https://example.com/x"}
        assert web_citation_link(c) == ("https://example.com/x", "https://example.com/x")

    def test_mapping_senza_url_e_none(self):
        assert web_citation_link({"title": "senza url"}) is None

    def test_tipo_ignoto_e_none(self):
        assert web_citation_link(123) is None


class TestProvenanceSummary:
    def test_solo_corpus(self):
        label, key = provenance_summary([("doc1", 1)], [])
        assert key == "corpus"
        assert "Catalogo" in label

    def test_solo_web(self):
        label, key = provenance_summary([], ["https://example.com/a"])
        assert key == "web"
        assert "NON verificata" in label

    def test_misto(self):
        label, key = provenance_summary([("doc1", 1)], ["https://example.com/a"])
        assert key == "misto"
        assert "NON verificata" in label

    def test_nessuna_fonte(self):
        label, key = provenance_summary([], [])
        assert key == "nessuna"


def test_web_warning_menziona_non_verificata():
    assert "non verificat" in WEB_WARNING.lower()
