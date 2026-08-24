"""Epica H — la cascata web CABLATA NEL GRAFO, e le quattro porte che la tengono chiusa.

Perche' questo file esiste, e perche' esercita il GRAFO
-------------------------------------------------------
Questo progetto ha gia' pagato tre volte lo stesso difetto: una guardia
scritta, testata in isolamento, verde — e mai cablata dove gira il sistema
(`singleton` 92eb955, RBAC 7b5d1ff, `hop2_warning` c3777d7). Il primo blocco
dell'epica H (1bd9a98) ha lasciato in casa un motore di ricerca, un Port e una
guardia sulle citazioni web: tutto testato, e NIENTE di collegato al grafo.
Quindi qui NON si chiamano le funzioni pure: si costruisce `build_graph` e si
guarda cosa esce da `invoke`.

Le tre affermazioni che questo file deve dimostrare
---------------------------------------------------
1. IL PREDICATO. "Il corpus ha fallito" ha UNA sola definizione: quella che
   `reconcile` gia' applica. `corpus_exhausted` la interroga invece di
   riscriverla, quindi le quattro situazioni in cui il web non deve MAI partire
   (`access_error`, `system_error`, `deleted_warning`, guardrail `BLOCK`)
   cadono fuori da sole. La tabella qui sotto le ASSERISCE tutte e sei, perche'
   "cadono fuori da sole" e' un ragionamento, e un ragionamento non e' una prova.
2. IL COMMIT E' SICURO. Con `web_search=None` il grafo dev'essere
   FUNZIONALMENTE IDENTICO a prima: stessi nodi, stesse risposte, stesso audit.
3. IL WEB NON PUO' MENTIRE. Un codice non a catalogo, un prezzo, una fonte non
   recuperata: ognuno di questi RIFIUTA la risposta web, non la annota.
"""

from __future__ import annotations

import pytest

from src.graph.pipeline import (ACCESS_MSG, BLOCK_MSG, SYS_UNAVAIL, Deps,
                                Evidence, build_graph, corpus_exhausted,
                                reconcile)
from src.graph.llm import FakeLLM
from src.retrieval.confidence import Level, Thresholds
from src.retrieval.synthesis import REFUSAL
from src.retrieval.websearch import FakeWebSearch, WebResult, WebSearchUnavailable

T = Thresholds(tau_hi=0.9, tau_lo=0.0054)

DOC, PAGE = "hurth_hsw_630", 12
EV = Evidence(doc=DOC, page=PAGE, lang="IT", code="877615", desc="PISTON PIN",
              text=None, qty=1, price=None, type="table_row", status="clean",
              warnings=[], score=9.0)
EV2 = EV._replace(code="877616", desc="RING", score=2.0)

URL_A = "https://example-oem.test/manuale/hsw630"
URL_B = "https://parts.example.test/scheda/877615"

RES = [WebResult(title="Manuale HSW 630", url=URL_A,
                 snippet="La coppia di serraggio del coperchio e' 45 Nm.",
                 engine="duckduckgo", score=1.0),
       WebResult(title="Scheda ricambio", url=URL_B,
                 snippet="Spinotto pistone 877615.", engine="brave", score=0.9)]


def _deps(*, web=None, llm=None, catalog=None, retrieve=None, synthesize=None):
    """Deps minime: nessun ramo SQL/visivo, cosi' la rotta e' sempre narrativa."""
    return Deps(
        retrieve=retrieve or (lambda *a, **k: ([], [])),
        synthesize=synthesize or (lambda q, ev: REFUSAL),
        catalog_lookup=catalog or (lambda k: False),
        thresholds=T,
        llm=llm,
        web_search=web,
    )


def _gira(deps: Deps, query: str = "coppia di serraggio del coperchio",
          role: str = "tecnico") -> dict:
    return build_graph(deps).invoke({"query": query, "role": role})


# ---------------------------------------------------------------------------
# 1. IL PREDICATO — una sola definizione di "il corpus ha fallito"
# ---------------------------------------------------------------------------

class TestCorpusExhausted:
    """Tabella su TUTTI gli stati che `reconcile` sa produrre.

    Ogni riga e' (nome, stato, il web puo' partire?). Le quattro esclusioni non
    sono "assunte perche' il codice e' elegante": sono asserite una per una.
    """

    CASI = [
        # --- il web NON deve MAI partire ------------------------------------
        ("access_error: un rifiuto RBAC non si aggira cercando su internet — "
         "sarebbe una fuga di dati per progettazione",
         {"access_error": True, "level": "BASSA"}, False),
        ("system_error: Ollama giu' — il ramo web ha bisogno dello STESSO "
         "modello per sintetizzare, quindi non puo' funzionare",
         {"system_error": True, "level": "BASSA"}, False),
        ("deleted_warning: li' una risposta c'e' (con avviso), non e' un vuoto",
         {"deleted_warning": True, "level": "BASSA", "answer": REFUSAL}, False),
        ("guardrail BLOCK: il guardrail ha bloccato un codice non confermato; "
         "cercarlo sul web e servirlo e' esattamente lo 'sbagliato in silenzio'",
         {"level": "ALTA", "guardrail_gate": "BLOCK", "answer": "il codice e' 999999"},
         False),
        ("guardrail VERIFY: una risposta c'e', a MEDIA",
         {"level": "ALTA", "guardrail_gate": "VERIFY", "answer": "X"}, False),
        ("PASS: la risposta del corpus e' buona",
         {"level": "ALTA", "guardrail_gate": "PASS", "answer": "X"}, False),
        ("obsolete_warning su risposta valida: c'e' una risposta",
         {"level": "MEDIA", "guardrail_gate": "PASS", "answer": "X",
          "obsolete_warning": True}, False),
        # --- il web PUO' partire: il corpus ha davvero detto "non lo so" -----
        ("livello BASSA: nessuna evidenza recuperata -> rifiuto puro",
         {"level": "BASSA"}, True),
        ("l'LLM ha rifiutato pur con retrieval MEDIA -> rifiuto puro",
         {"level": "MEDIA", "guardrail_gate": "PASS", "answer": REFUSAL}, True),
    ]

    @pytest.mark.parametrize("nome,stato,atteso",
                             CASI, ids=[c[0][:40] for c in CASI])
    def test_tabella(self, nome, stato, atteso):
        assert corpus_exhausted(stato) is atteso, nome

    def test_e_davvero_reconcile_a_decidere(self):
        """Il predicato non ha una seconda definizione: chiama `reconcile`."""
        for _, stato, atteso in self.CASI:
            out = reconcile(stato)
            e_rifiuto_puro = (out["final_level"] == "BASSA"
                              and out["final_answer"] == REFUSAL)
            assert e_rifiuto_puro is atteso

    def test_uguaglianza_non_sottostringa(self):
        """`deleted_warning` produce REFUSAL **piu'** l'avviso: con un `in` al
        posto dell'uguaglianza il web partirebbe su un documento eliminato,
        cancellando l'avviso. E' la lezione ac8da1a."""
        out = reconcile({"deleted_warning": True, "level": "BASSA"})
        assert REFUSAL in out["final_answer"]          # la sottostringa c'e'
        assert out["final_answer"] != REFUSAL          # ma non e' un rifiuto puro
        assert corpus_exhausted({"deleted_warning": True, "level": "BASSA"}) is False


# ---------------------------------------------------------------------------
# 2. IL CABLAGGIO — e la prova che con web_search=None non cambia NULLA
# ---------------------------------------------------------------------------

class TestCablaggio:

    def test_senza_motore_il_grafo_non_ha_nemmeno_i_nodi(self):
        nodi = set(build_graph(_deps()).get_graph().nodes)
        assert "web" not in nodi and "triage" not in nodi

    def test_con_motore_i_nodi_esistono(self):
        nodi = set(build_graph(_deps(web=FakeWebSearch(RES),
                                     llm=FakeLLM("x"))).get_graph().nodes)
        assert "web" in nodi and "triage" in nodi

    def test_senza_motore_il_rifiuto_resta_il_rifiuto(self):
        out = _gira(_deps())
        assert out["final_level"] == "BASSA"
        assert out["final_answer"] == REFUSAL
        assert not any("web" in a for a in out["audit"])

    def test_il_web_non_parte_se_il_corpus_risponde(self):
        motore = FakeWebSearch(RES)
        deps = _deps(web=motore, llm=FakeLLM("x"),
                     retrieve=lambda *a, **k: ([EV, EV2], []),
                     synthesize=lambda q, ev: f"La coppia e' 45 Nm ({DOC}, pag. {PAGE}).")
        out = _gira(deps)
        assert out["final_level"] in ("ALTA", "MEDIA")
        assert motore.calls == [], "il web e' partito con una risposta buona in mano"

    def test_il_web_non_parte_su_access_error(self):
        motore = FakeWebSearch(RES)
        out = build_graph(_deps(web=motore, llm=FakeLLM("x"))).invoke(
            {"query": "coppia", "role": "ruolo_inventato"})
        assert out["final_answer"] == ACCESS_MSG
        assert motore.calls == [], "RBAC aggirato dal web: fuga di dati"

    def test_il_web_non_parte_su_system_error(self):
        motore = FakeWebSearch(RES)
        def boom(*a, **k):
            raise RuntimeError("qdrant giu'")
        out = _gira(_deps(web=motore, llm=FakeLLM("x"), retrieve=boom))
        assert out["final_answer"] == SYS_UNAVAIL
        assert motore.calls == []

    def test_il_web_non_parte_su_guardrail_block(self):
        """Il caso piu' pericoloso: il guardrail ha bloccato un codice non
        confermato. Cercare QUEL codice sul web e servirlo sarebbe il difetto
        che il prodotto vieta."""
        motore = FakeWebSearch(RES)
        deps = _deps(web=motore, llm=FakeLLM("x"),
                     retrieve=lambda *a, **k: ([EV, EV2], []),
                     synthesize=lambda q, ev: f"Il codice e' 999999 ({DOC}, pag. {PAGE}).")
        out = _gira(deps)
        assert out["final_answer"] == BLOCK_MSG
        assert motore.calls == [], "il web ha inseguito un codice BLOCCATO"

    def test_web_done_impedisce_di_avvitarsi(self):
        """Senza il flag, un web che non trova nulla rimanda a un rifiuto che
        rimanda al web: il grafo gira su se stesso."""
        motore = FakeWebSearch([])
        out = _gira(_deps(web=motore, llm=FakeLLM("x")))
        assert out["web_done"] is True
        assert len(motore.calls) == 1
        assert out["final_answer"] == REFUSAL


# ---------------------------------------------------------------------------
# 3. IL LIVELLO — distinto, mai ALTA, mai confuso con MEDIA
# ---------------------------------------------------------------------------

class TestLivelloWeb:

    def _ok(self, **kw):
        deps = _deps(web=FakeWebSearch(RES),
                     llm=FakeLLM(f"La coppia e' 45 Nm (web: {URL_A})."), **kw)
        return _gira(deps)

    def test_livello_distinto(self):
        out = self._ok()
        assert out["final_level"] == Level.WEB.value == "WEB"
        assert out["final_level"] not in ("ALTA", "MEDIA")

    def test_la_risposta_dichiara_di_venire_dal_web(self):
        out = self._ok()
        assert "WEB" in out["final_answer"].upper()
        assert URL_A in out["final_answer"]

    def test_le_citazioni_di_corpus_non_sopravvivono(self):
        out = self._ok()
        assert out["citations"] == []
        assert out["web_citations"] == [URL_A]

    def test_rango_fra_bassa_e_media_nel_gate(self):
        from scripts.golden_gate import get_level_rank
        assert (get_level_rank("BASSA") < get_level_rank("WEB")
                < get_level_rank("MEDIA") < get_level_rank("ALTA"))
        assert get_level_rank("qualcosa") == 0


# ---------------------------------------------------------------------------
# 4. CODICI E PREZZI DAL WEB
# ---------------------------------------------------------------------------

class TestCodiciEPrezzi:

    def _gira_con(self, testo, *, catalog=lambda k: False):
        deps = _deps(web=FakeWebSearch(RES), llm=FakeLLM(testo), catalog=catalog)
        return _gira(deps)

    def test_codice_ASSENTE_dal_catalogo_rifiuta_la_risposta(self):
        """Un avviso a schermo non protegge: un codice si copia e incolla e
        sopravvive al contesto. Codice sbagliato = pezzo sbagliato ordinato."""
        out = self._gira_con(f"Il ricambio e' il codice 998877 (web: {URL_A}).")
        assert out["final_answer"] == REFUSAL
        assert out["final_level"] == "BASSA"
        assert "998877" not in out["final_answer"]

    def test_codice_confermato_dal_NOSTRO_catalogo_passa_ed_e_etichettato(self):
        """Il caso piu' prezioso dell'epica: il web ha dato il numero e i nostri
        dati lo confermano (MISS_RETRIEVAL = esiste a catalogo, non recuperato)."""
        out = self._gira_con(f"Il ricambio e' il codice 877615 (web: {URL_A}).",
                             catalog=lambda k: k.upper() == "877615")
        assert out["final_level"] == "WEB"
        assert "877615" in out["final_answer"]
        assert "catalogo" in out["final_answer"].lower()

    def test_lookup_catalogo_rotto_rifiuta(self):
        """Se il catalogo non risponde non possiamo dire ne' 'esiste' ne' 'non
        esiste': fail-closed, mai etichettare come confermato cio' che non si e'
        potuto verificare."""
        def rotto(k):
            raise RuntimeError("postgres giu'")
        out = self._gira_con(f"Il ricambio e' il codice 877615 (web: {URL_A}).",
                             catalog=rotto)
        assert out["final_answer"] == REFUSAL

    @pytest.mark.parametrize("testo", [
        "Il pezzo costa 273,52 EUR",
        "Prezzo: €273.52",
        "Il listino indica $300",
        "Costo indicativo 45 euro",
        "Price: 120 USD",
    ])
    def test_qualunque_prezzo_dal_web_rifiuta(self, testo):
        """`verify_price_grounding` ancora ogni prezzo al DB, e sul web non c'e'
        DB. Regola v1 volutamente grossolana."""
        out = self._gira_con(f"{testo} (web: {URL_A}).")
        assert out["final_answer"] == REFUSAL, f"prezzo passato: {testo!r}"

    @pytest.mark.parametrize("testo", [
        "Il ricambio e' 273,52 €",          # solo il simbolo, nessuna parola
        "Der Preis betragt 40",             # solo la parola, nessun simbolo
    ])
    def test_basta_il_simbolo_o_basta_la_parola(self, testo):
        out = self._gira_con(f"{testo} (web: {URL_A}).")
        assert out["final_answer"] == REFUSAL

    def test_risposta_senza_prezzo_ne_codici_passa(self):
        out = self._gira_con(f"La coppia di serraggio e' 45 Nm (web: {URL_A}).")
        assert out["final_level"] == "WEB"

    def test_LIMITE_NOTO_un_numero_nudo_senza_valuta_passa(self):
        """Il buco che la regola v1 lascia aperto, scritto qui perche' sia una
        DECISIONE e non una svista.

        Un importo senza alcun marcatore ("il ricambio viene 273,52") passa: la
        regola guarda i token in forma di VALUTA, e un decimale nudo non lo e'.
        Allargarla a "qualunque decimale" rifiuterebbe meta' delle risposte
        tecniche vere (quote, coppie, tolleranze: 45.50 mm, 12,75 Nm), cioe'
        pagherebbe il ramo web per intero per chiudere un caso che il prompt di
        sistema gia' vieta (regola 4) e che nessuna frase naturale in IT/EN/DE
        produce senza scrivere anche costa/prezzo/EUR/€.
        Se un giorno se ne vedra' uno vero sul corpus, questo test cambia segno.
        """
        out = self._gira_con(f"Il ricambio viene 273,52 (web: {URL_A}).")
        assert out["final_level"] == "WEB"


class TestNessunaContaminazioneDiStato:
    """Il ramo web arriva DOPO che il ramo del corpus ha gia' scritto in stato.

    Sono i due modi in cui una risposta web potrebbe ereditare un giudizio dato
    su un'ALTRA risposta — e sarebbero entrambi bugie a schermo.
    """

    LLM = f"La coppia e' 45 Nm (web: {URL_A})."

    def test_il_gate_del_guardrail_del_corpus_non_si_applica_al_web(self):
        """Evidenza OCR/degraded -> guardrail VERIFY sul ramo corpus. Se quel
        gate sopravvivesse, la risposta web uscirebbe con "(verifica
        consigliata)" a MEDIA: cioe' col livello del catalogo."""
        degradata = EV._replace(status="degraded")
        deps = _deps(web=FakeWebSearch(RES), llm=FakeLLM(self.LLM),
                     retrieve=lambda *a, **k: ([degradata, EV2], []),
                     synthesize=lambda q, ev: REFUSAL)
        out = _gira(deps)
        assert out["guardrail_gate"] == "VERIFY"      # il corpus l'ha davvero scritto
        assert out["final_level"] == "WEB"
        assert "(verifica consigliata)" not in out["final_answer"]

    def test_avviso_documentazione_SOSTITUITA_non_finisce_su_una_pagina_web(self):
        """`obsolete_warning` resta in stato quando il fallback trova evidenza
        ma la confidenza resta BASSA. Appenderlo a una pagina di internet
        significherebbe dichiararla 'documentazione sostituita' del cliente."""
        deps = Deps(retrieve=lambda *a, **k: ([], []),
                    synthesize=lambda q, ev: REFUSAL,
                    catalog_lookup=lambda k: False, thresholds=T,
                    retrieve_superseded=lambda *a, **k: ([EV._replace(score=0.0)], []),
                    llm=FakeLLM(self.LLM), web_search=FakeWebSearch(RES))
        out = _gira(deps)
        assert out["obsolete_warning"] is True        # e' davvero in stato
        assert out["final_level"] == "WEB"
        assert "SOSTITUITA" not in out["final_answer"]


# ---------------------------------------------------------------------------
# 5. LE FONTI — la guardia in uscita vale anche dentro il grafo
# ---------------------------------------------------------------------------

class TestFonti:

    def test_url_inventato_sparisce_dalla_risposta(self):
        deps = _deps(web=FakeWebSearch(RES), llm=FakeLLM(
            f"La coppia e' 45 Nm (web: {URL_A}). Vedi anche "
            "https://sito-mai-visto.test/pagina ."))
        out = _gira(deps)
        assert "sito-mai-visto" not in out["final_answer"]
        assert out["web_citations"] == [URL_A]

    def test_zero_fonti_verificate_rifiuta(self):
        """Mai un'affermazione senza fonte, e una fonte non recuperata non e'
        una fonte."""
        deps = _deps(web=FakeWebSearch(RES),
                     llm=FakeLLM("La coppia e' 45 Nm (web: https://inventato.test/x)."))
        out = _gira(deps)
        assert out["final_answer"] == REFUSAL

    def test_motore_indisponibile_lascia_il_rifiuto_del_corpus(self):
        """Il web che si rompe non deve trasformare 'non lo so' in 'sistema non
        disponibile': il corpus aveva gia' risposto, e la sua risposta era il
        rifiuto."""
        motore = FakeWebSearch(error=WebSearchUnavailable("searxng spento"))
        out = _gira(_deps(web=motore, llm=FakeLLM("x")))
        assert out["final_answer"] == REFUSAL
        assert out.get("system_error") is not True

    def test_llm_giu_lascia_il_rifiuto_del_corpus(self):
        deps = _deps(web=FakeWebSearch(RES),
                     llm=FakeLLM(error=RuntimeError("ollama giu'")))
        out = _gira(deps)
        assert out["final_answer"] == REFUSAL

    def test_llm_che_rifiuta_resta_un_rifiuto(self):
        out = _gira(_deps(web=FakeWebSearch(RES), llm=FakeLLM(REFUSAL)))
        assert out["final_answer"] == REFUSAL

    def test_le_fonti_arrivano_al_modello_confinate_e_dichiarate_non_fidate(self):
        llm = FakeLLM(f"La coppia e' 45 Nm (web: {URL_A}).")
        _gira(_deps(web=FakeWebSearch(RES), llm=llm))
        assert llm.calls, "il modello non e' stato chiamato"
        _, user = llm.calls[-1]
        assert '<fonti_web fidato="no">' in user


# ---------------------------------------------------------------------------
# 6. L'INTERRUTTORE — default OFF, e dichiarato
# ---------------------------------------------------------------------------

class TestInterruttore:

    def test_default_off(self, monkeypatch):
        from src.serving.session_deps import _web_cascade_enabled
        monkeypatch.delenv("AIKE_WEB_CASCADE", raising=False)
        assert _web_cascade_enabled() is False

    @pytest.mark.parametrize("val,atteso", [("1", True), ("true", True),
                                            ("0", False), ("off", False),
                                            ("", False)])
    def test_valori(self, monkeypatch, val, atteso):
        from src.serving.session_deps import _web_cascade_enabled
        monkeypatch.setenv("AIKE_WEB_CASCADE", val)
        assert _web_cascade_enabled() is atteso

    def test_dichiarato_nella_configurazione_del_run(self, monkeypatch):
        """Ogni misura del repo e' stata presa col web spento: un audit che non
        dichiara l'interruttore non e' piu' interpretabile."""
        from src.common.run_config import effective_config, format_config_banner
        monkeypatch.delenv("AIKE_WEB_CASCADE", raising=False)
        cfg = effective_config(_deps())
        assert cfg["web_cascade"]["enabled"] is False
        assert cfg["web_cascade"]["reason"]
        assert cfg["web_cascade"]["wired"] is False   # osservato dalle deps
        assert "web" in format_config_banner(cfg).lower()

    def test_osservato_non_previsto(self, monkeypatch):
        from src.common.run_config import effective_config
        monkeypatch.setenv("AIKE_WEB_CASCADE", "1")
        cfg = effective_config(_deps())      # env ON ma deps SENZA motore
        assert cfg["web_cascade"]["enabled"] is True
        assert cfg["web_cascade"]["wired"] is False
