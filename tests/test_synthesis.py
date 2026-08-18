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
    synthesize,
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


class TestFormatoCitazioneObbligatorio:
    """Invariante di formato: il prompt puo' cambiare, il parser no.

    Il nodo di sintesi ricava le citazioni SOLO con `_CITE` (doc, pag. N con N
    numerico). Una citazione che nomina il foglio Excel al posto del numero di
    pagina NON viene riconosciuta: le citazioni diventano 0 e la risposta
    ricade a BASSA. Questi test impediscono che il prompt torni a suggerire
    quella forma.
    """

    def test_nome_foglio_non_e_una_citazione_valida(self):
        t = ("La guarnizione e' a listino "
             "(Electrolux_Distinta_Ricambi.xlsx, foglio PARTS LIST - SPANISH).")
        assert extract_citations(t) == []

    def test_parola_foglio_al_posto_del_numero_non_passa(self):
        assert extract_citations("(listino_ricambi.xlsx, pag. Sheet1)") == []
        assert extract_citations("(listino_ricambi.xlsx, foglio 3)") == []

    def test_forma_obbligatoria_con_pagina_numerica_passa(self):
        # anche pag. 0 (tipico dei fogli di calcolo) e' una citazione valida
        t = ("Aluminum handle, quantita 1; codice non indicato in questa fonte "
             "(DeliTools_SpareParts_List.xlsx, pag. 0).")
        assert extract_citations(t) == [("DeliTools_SpareParts_List.xlsx", 0)]

    def test_esempi_del_prompt_sono_parsabili_dal_parser(self):
        # ogni citazione-esempio scritta nel system prompt deve essere
        # riconosciuta: un esempio non parsabile insegnerebbe al modello una
        # forma che azzera le citazioni.
        esempi = extract_citations(SYSTEM_SYNTHESIS)
        assert ("rotax_912_914_IPC", 22) in esempi
        assert ("listino_ricambi.xlsx", 0) in esempi

    def test_prompt_impone_pagina_numerica_e_vieta_il_foglio(self):
        assert "(nome_documento, pag. N)" in SYSTEM_SYNTHESIS
        assert "foglio" in SYSTEM_SYNTHESIS  # il caso Excel e' trattato esplicitamente

    def test_prompt_distingue_evidenza_assente_da_dato_mancante(self):
        # non deve tornare a far coincidere "manca il codice" con "rifiuta"
        assert "QUANDO NON rifiutare" in SYSTEM_SYNTHESIS

    def test_prompt_lega_N_all_attributo_pag(self):
        # N non e' "un numero qualsiasi": e' il valore dell'attributo pag del
        # <documento> usato. L'enfasi generica ("e' SEMPRE un numero") ha fatto
        # scegliere il CODICE RICAMBIO come pagina (gate C_EN_03).
        assert "attributo pag" in SYSTEM_SYNTHESIS
        assert "SEMPRE un numero" not in SYSTEM_SYNTHESIS

    def test_prompt_vieta_il_codice_al_posto_della_pagina(self):
        assert "NON sono numeri di pagina" in SYSTEM_SYNTHESIS
        for parola in ("codice ricambio", "prezzo", "quantita", "posizione"):
            assert parola in SYSTEM_SYNTHESIS


class _FakeLLM:
    """LLM finto: nessuna rete, registra il system prompt ricevuto."""

    def __init__(self, out: str):
        self.out = out
        self.seen: list[tuple[str, str]] = []

    def generate(self, system: str, user: str) -> str:
        self.seen.append((system, user))
        return self.out


class TestRifiutoRestaBassa:
    def test_synthesize_passa_il_system_prompt_e_ritorna_il_testo(self):
        llm = _FakeLLM(REFUSAL)
        out = synthesize(llm, "che codice ha lo spinotto?", [ROW])
        assert out == REFUSAL
        assert llm.seen[0][0] == SYSTEM_SYNTHESIS
        assert "<domanda>" in llm.seen[0][1]

    def test_refusal_non_produce_citazioni(self):
        assert extract_citations(REFUSAL) == []

    def test_refusal_ricade_a_bassa_in_reconcile(self):
        from src.graph.pipeline import reconcile
        r = reconcile({"level": "MEDIA", "answer": REFUSAL, "citations": []})
        assert r["final_level"] == "BASSA" and r["final_answer"] == REFUSAL

    def test_risposta_parziale_citata_resta_media(self):
        from src.graph.pipeline import reconcile
        ans = ("Aluminum handle, quantita 1; codice non indicato in questa fonte "
               "(DeliTools_SpareParts_List.xlsx, pag. 0).")
        r = reconcile({"level": "MEDIA", "answer": ans,
                       "citations": extract_citations(ans)})
        assert r["final_level"] == "MEDIA" and r["final_answer"] == ans
