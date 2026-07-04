"""P3.4 — Test delle parti PURE della sintesi ancorata (nessun LLM).

Grounding in tag XML (l'evidenza non si confonde con la domanda), citazioni
parsabili, rifiuto esatto. La chiamata al modello e' integrazione (spike).
"""

from __future__ import annotations

from src.retrieval.synthesis import (
    REFUSAL,
    SYSTEM_SYNTHESIS,
    build_prompt,
    extract_citations,
    format_evidence_xml,
    strip_citations,
)

ROW = {"type": "table_row", "doc": "rotax_912_914_IPC", "page": 22, "lang": "EN",
       "code": "877615", "desc": "PISTON PIN", "qty": 1, "price": None}
TXT = {"type": "text", "doc": "stoll_CMS830C_spareparts", "page": 12, "lang": "DE",
       "text": "Wartung alle 500 Stunden."}


class TestEvidenzaXML:
    def test_attributi_documento(self):
        xml = format_evidence_xml([ROW])
        assert '<documento doc="rotax_912_914_IPC" pag="22" lang="EN">' in xml
        assert "877615" in xml and "PISTON PIN" in xml

    def test_riga_tabella_con_qty(self):
        assert "qty 1" in format_evidence_xml([ROW])

    def test_testo_semplice(self):
        xml = format_evidence_xml([TXT])
        assert "Wartung alle 500 Stunden." in xml and 'pag="12"' in xml

    def test_evidenza_vuota(self):
        assert format_evidence_xml([]) == "<evidenza></evidenza>"

    def test_escaping_xml(self):
        xml = format_evidence_xml([{"type": "text", "doc": "a&b", "page": 1,
                                    "lang": "IT", "text": "5 < 6 & ok"}])
        assert "&amp;" in xml and "&lt;" in xml
        assert "a&b" not in xml  # l'attributo grezzo non deve comparire

    def test_niente_none_grezzo(self):
        # price None non deve stampare 'None'
        assert "None" not in format_evidence_xml([ROW])


class TestCitazioni:
    def test_estrae_doc_pagina(self):
        t = "Lo spinotto è il 877615 (rotax_912_914_IPC, pag. 22)."
        assert extract_citations(t) == [("rotax_912_914_IPC", 22)]

    def test_multipla(self):
        t = "(doc_a, pag. 3) ... (doc_b, pag. 10)"
        assert extract_citations(t) == [("doc_a", 3), ("doc_b", 10)]

    def test_nessuna_citazione(self):
        assert extract_citations("nessuna fonte qui") == []

    def test_tollerante_pag_senza_punto(self):
        assert extract_citations("(doc_x, pag 7)") == [("doc_x", 7)]

    def test_toglie_etichetta_doc_prefissata(self):
        # il modello a volte scrive "(doc NOME, pag. 1)": l'etichetta va tolta
        assert extract_citations("(doc ave_listino, pag. 1)") == [("ave_listino", 1)]
        assert extract_citations("(documento rotax_912, pag. 22)") == [("rotax_912", 22)]

    def test_toglie_forma_attributo_xml(self):
        # il modello a volte copia la forma XML: (doc="NOME", pag. N)
        assert extract_citations('(doc="ave_listino", pag. 1)') == [("ave_listino", 1)]


class TestStripCitazioni:
    def test_rimuove_span_citazione(self):
        # il nome-doc (con sotto-token code-shaped) non deve restare al guardrail
        t = "Part 71910 è ok (lycoming_IGO540_PC116, pag. 88)."
        s = strip_citations(t)
        assert "IGO540" not in s and "71910" in s

    def test_niente_citazioni_invariato(self):
        assert strip_citations("nessuna fonte") == "nessuna fonte"


class TestPrompt:
    def test_build_prompt_contiene_blocchi(self):
        p = build_prompt("che codice ha lo spinotto?", [ROW])
        assert "<evidenza>" in p and "</evidenza>" in p
        assert "<domanda>che codice ha lo spinotto?</domanda>" in p

    def test_system_ha_regole_e_rifiuto(self):
        assert REFUSAL in SYSTEM_SYNTHESIS
        assert "<evidenza>" in SYSTEM_SYNTHESIS  # ancora il grounding ai tag
