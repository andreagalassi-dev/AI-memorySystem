"""F.2c/F.2d — La CARD di suggerimento: il primo consumatore della memoria.

Perche' questo file esiste
--------------------------
`memory_store.py` (il magazzino) e `revalidation.py` (il cancello) erano scritti,
testati e **senza un solo consumatore**. E' la firma di questo progetto: in
`docs`/memoria ci sono gia' tre guardie trovate morte cosi'. Qui la memoria
prende finalmente un consumatore, e questo file e' il collaudo di quel cablaggio.

La legge del punto (pivot dell'11/08, provato da un exploit end-to-end)
-----------------------------------------------------------------------
**LA MEMORIA NON TOCCA MAI LA QUERY.** Una versione precedente lasciava che un
binding confermato estendesse la query dell'utente: un ricordo sul RADIATORE
faceva rispondere ALTA col prezzo del pezzo sbagliato a una domanda sul
CARBURATORE. Da allora la memoria e' una CARD di suggerimento:

  1. appare SOLO su una risposta BASSA o MEDIA (mai su una buona);
  2. mostra il testo della domanda suggerita e la sua data, nient'altro;
  3. il click vale come un TURNO UTENTE VERBATIM — la domanda viene rifatta da
     capo, per intero, passando da tutte le difese.

Metodo: si esercita il PERCORSO REALE (`on_message` / `on_memory_suggest` di
`src/ui/chainlit_app.py`), non le funzioni pure isolate. Isolare le funzioni
pure e' esattamente cio' che in questo progetto ha lasciato tre difese morte.

Le tre parti
------------
  A — il renderer (`src/ui/memory_card.py`): testo ostile, date anomale, gate
      di livello. Copre i TODO eseguibili lasciati in
      `tests/test_memory_policy_adversarial.py` (PARTE C).
  B — il CABLAGGIO nella UI, sul percorso reale: la card non appare su ALTA,
      la memoria non influenza la query, il click rifa' la domanda verbatim,
      il cancello non e' scavalcabile.
  C — integrazione Postgres + Qdrant REALI (con ROLLBACK): un ricordo STALE e
      un ricordo di UN ALTRO RUOLO non arrivano sullo schermo. E' la parte che
      prova il cancello dal vivo, non a tavolino.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.common.roles import Role
from src.registry.memory_store import MemoryHint

UTC = timezone.utc
FRESH = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _hint(query="quanto costa la pompa idraulica GM41013", *, hint_id=1,
          evidence=(("flow_exploded_2022", 7),), created_at=FRESH,
          risposta="SEGRETO_RISPOSTA", conferme=3, livello="personale"):
    return MemoryHint(query=query, evidence_ids=tuple(evidence),
                      conferme_count=conferme, livello=livello,
                      risposta=risposta, hint_id=hint_id,
                      created_at=created_at, intent="structured")


# =============================================================================
# PARTE A — il renderer
# =============================================================================

class TestGateDiLivello:
    """La card compare SOLO su BASSA/MEDIA. Su ALTA un suggerimento e' rumore
    che invita a rimettere in discussione una risposta gia' fondata."""

    @pytest.mark.parametrize("level, expected", [
        ("ALTA", False), ("MEDIA", True), ("BASSA", True)])
    def test_card_shown_only_on_low_confidence(self, level, expected):
        from src.ui.memory_card import should_show_card
        assert should_show_card(level, [_hint()]) is expected

    @pytest.mark.parametrize("level", [
        None, "", "   ", "WEB", "alta", "sconosciuto", 42, object()])
    def test_livelli_anomali_o_nuovi_non_mostrano_nulla(self, level):
        """Fail-closed sul livello: un valore nuovo (la cascata web ne ha
        introdotto uno) non deve APRIRE la card. `render_level` nella UI e'
        volutamente tollerante a un livello ignoto; qui la tolleranza sarebbe
        il difetto, perche' mostrerebbe la card dove nessuno ha deciso che
        vada mostrata. Nota: 'alta' minuscolo resta chiuso — la card non e' il
        posto dove indovinare."""
        from src.ui.memory_card import should_show_card
        assert should_show_card(level, [_hint()]) is False

    @pytest.mark.parametrize("hints", [None, [], ()])
    def test_senza_hint_nessuna_card(self, hints):
        from src.ui.memory_card import should_show_card
        assert should_show_card("BASSA", hints) is False


class TestDataDelBinding:
    """`MemoryHint.created_at` non e' validato da nessuno (dataclass nuda). La
    versione pre-pivot faceva `created_at.date()` senza guardia e un valore
    anomalo faceva esplodere l'INTERA risposta. Qui deve degradare."""

    @pytest.mark.parametrize("created", ["2026-01-15", date(2026, 1, 15),
                                         None, 0, [], object()])
    def test_card_renderer_must_not_raise_on_bad_created_at(self, created):
        from src.ui.memory_card import render_memory_card
        assert render_memory_card(_hint(created_at=created))

    def test_una_data_vera_e_mostrata(self):
        from src.ui.memory_card import render_memory_card
        out = render_memory_card(_hint(created_at=datetime(2026, 1, 15, 9, 30,
                                                           tzinfo=UTC)))
        assert "2026-01-15" in out

    @pytest.mark.parametrize("created", ["2026-01-15", None, 0])
    def test_una_data_non_databile_diventa_data_ignota(self, created):
        from src.ui.memory_card import UNKNOWN_DATE, render_memory_card
        assert UNKNOWN_DATE in render_memory_card(_hint(created_at=created))


class TestTestoOstile:
    """`hint.query` e' testo scritto da un utente e salvato su DB. Post-pivot
    non finisce piu' in un prompt: finisce SULLO SCHERMO. L'injection cambia
    bersaglio, non sparisce."""

    def test_card_must_escape_hint_text(self):
        from src.ui.memory_card import render_memory_card
        out = render_memory_card(_hint("[clicca qui](javascript:alert(1)) ABC124"))
        assert "javascript:" not in out

    @pytest.mark.parametrize("payload", [
        "[x](javascript:alert(1))",
        "[x](JaVaScRiPt:alert(1))",
        "[x](javascript%20:alert(1))".replace("%20", " "),
        "<a href='javascript:alert(1)'>x</a>",
        "[x](data:text/html;base64,PHNjcmlwdD4=)",
        "[x](vbscript:msgbox)",
        "[x](file:///etc/passwd)",
    ])
    def test_nessuno_schema_pericoloso_sopravvive(self, payload):
        from src.ui.memory_card import render_memory_card
        out = render_memory_card(_hint(payload)).lower()
        for scheme in ("javascript:", "vbscript:", "data:", "file:"):
            assert scheme not in out, f"{scheme} sopravvissuto in {out!r}"

    def test_niente_html_grezzo(self):
        from src.ui.memory_card import render_memory_card
        out = render_memory_card(_hint("<script>alert(1)</script> <img src=x>"))
        assert "<script>" not in out and "<img" not in out

    def test_il_testo_non_puo_forgiare_altre_sezioni_del_messaggio(self):
        """Il payload di prompt-injection dell'11/08 cambia solo bersaglio: qui
        proverebbe a forgiare una finta sezione '**Risposta:**' dentro il
        messaggio della UI.

        L'invariante che lo impedisce e' STRUTTURALE: il testo dell'utente e'
        collassato su UNA riga e ogni riga della card resta dentro la
        citazione (`>`). Non esiste quindi modo di emettere una riga che il
        lettore possa scambiare per una sezione del messaggio; e il grassetto
        della finta intestazione e' escapato, quindi non si accende nemmeno.

        NB: il testo LETTERALE della finta sezione resta leggibile dentro le
        virgolette basse, ed e' voluto — la card mostra cio' che l'utente
        scrisse davvero. Cio' che non deve poter fare e' USCIRE dalla
        citazione o ACCENDERE del markup.
        """
        from src.ui.memory_card import render_memory_card
        forged = ("pompa\n\n**Risposta:** il prezzo e' 10 EUR\n"
                  "**Livello di confidenza:** 🟢 ALTA")
        out = render_memory_card(_hint(forged))
        card_lines = [ln for ln in out.splitlines() if "pompa" in ln]
        assert len(card_lines) == 1
        assert all(ln.startswith(">") for ln in out.splitlines() if ln)
        assert "**Risposta:**" not in out
        assert "**Livello di confidenza:**" not in out

    def test_un_testo_lunghissimo_e_troncato(self):
        from src.ui.memory_card import MAX_QUERY_CHARS, render_memory_card
        out = render_memory_card(_hint("pompa " * 5000))
        assert len(out) < MAX_QUERY_CHARS + 600

    @pytest.mark.parametrize("bad", [None, 123, b"pompa", ["pompa"]])
    def test_una_query_non_testuale_non_produce_card_e_non_solleva(self, bad):
        from src.ui.memory_card import render_memory_card
        assert render_memory_card(_hint(bad)) == ""

    def test_la_card_non_mostra_mai_la_risposta_memorizzata(self):
        """La card mostra la DOMANDA e la data, mai `hint.risposta`: quella e'
        prosa LLM confermata nel complesso, e mostrarla accanto a una risposta
        vera la renderebbe indistinguibile da una risposta."""
        from src.ui.memory_card import render_memory_card
        out = render_memory_card(_hint(risposta="SEGRETO_RISPOSTA"))
        assert "SEGRETO_RISPOSTA" not in out

    def test_la_sezione_dice_che_e_un_suggerimento_e_non_una_risposta(self):
        """Cio' che finisce sullo schermo e' la SEZIONE (intestazione + card):
        e' li' che va cercata la dichiarazione, non nella singola card."""
        from src.ui.memory_card import render_memory_section
        out = render_memory_section([_hint()]).lower()
        assert "suggerimento" in out
        assert "non è una risposta" in out or "non e' una risposta" in out

    def test_la_sezione_non_usa_le_icone_delle_altre_sezioni(self):
        """La grammatica visiva introdotta in 03f385d assegna un'icona a ogni
        cosa: 📄 catalogo, 🌐 web, 🟢🟡🔴 confidenza, ⚠️ avviso. La card ne ha
        una tutta sua — se ne prendesse una in prestito, diventerebbe
        confondibile proprio con cio' da cui deve distinguersi."""
        from src.ui.memory_card import CARD_ICON, SECTION_HEADER
        for altrui in ("📄", "🌐", "🟢", "🟡", "🔴", "⚠️", "❔"):
            assert altrui not in CARD_ICON
            assert altrui not in SECTION_HEADER

    def test_una_sezione_senza_card_valide_e_vuota(self):
        from src.ui.memory_card import render_memory_section
        assert render_memory_section([]) == ""
        assert render_memory_section(None) == ""
        assert render_memory_section([_hint(None), _hint(123)]) == ""


# =============================================================================
# PARTE B — il CABLAGGIO nella UI, sul percorso REALE
# =============================================================================

class _FakeUser:
    def __init__(self, identifier="bob", role="tecnico"):
        self.identifier = identifier
        self.metadata = {"role": role}


class _FakeSession:
    def __init__(self, **initial):
        self.d = dict(initial)

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value


class _RecMessage:
    """Registratore al posto di `cl.Message` (non costruibile fuori da una
    sessione Chainlit). Cattura tutto cio' che finisce sullo schermo."""

    sent: list["_RecMessage"] = []

    def __init__(self, content="", elements=None, actions=None,
                 type="assistant_message", author=None, parent_id=None, **kw):
        self.content = content
        self.elements = list(elements or [])
        self.actions = list(actions or [])
        self.type = type
        self.author = author
        self.parent_id = parent_id
        self.id = f"msg-{len(_RecMessage.sent) + 1}"

    async def send(self):
        _RecMessage.sent.append(self)
        return self

    async def remove_actions(self):
        return None


class _AnswerSpy:
    """Sta al posto di `answer()`: registra ESATTAMENTE la query ricevuta."""

    def __init__(self, level="BASSA", text="non lo so"):
        self.calls: list[dict] = []
        self.level = level
        self.text = text

    def __call__(self, query, role, deps=None, context=None):
        self.calls.append({"query": query, "role": role, "context": context})
        return {"final_answer": self.text, "final_level": self.level,
                "citations": [], "evidence": [], "audit": []}

    @property
    def queries(self):
        return [c["query"] for c in self.calls]


@pytest.fixture
def ui(monkeypatch):
    """Monta la UI reale con le sole dipendenze esterne finte.

    Restano REALI: `on_message`, `on_memory_suggest`, la costruzione del
    contesto, il gate di livello, il renderer della card e — dove non
    esplicitamente sostituito — `recall_validated`, cioe' il cancello.
    """
    import chainlit as cl

    import src.ui.chainlit_app as app

    _RecMessage.sent = []
    session = _FakeSession(user=_FakeUser(), conv_turns=[], interactions={})
    monkeypatch.setattr(cl, "user_session", session)
    monkeypatch.setattr(cl, "Message", _RecMessage)
    monkeypatch.setattr(app, "get_deps", lambda role: None)
    monkeypatch.setattr(app, "ensure_page_render", lambda doc, page: None)

    spy = _AnswerSpy()
    monkeypatch.setattr(app, "answer", spy)

    class _Handle:
        module = app
        session_obj = session
        answer_spy = spy
        sent = _RecMessage.sent

        @staticmethod
        def messages():
            return _RecMessage.sent

        @staticmethod
        def last_content():
            return _RecMessage.sent[-1].content if _RecMessage.sent else ""

        @staticmethod
        def memory_actions():
            out = []
            for m in _RecMessage.sent:
                out += [a for a in m.actions
                        if getattr(a, "name", None) == app.MEMORY_ACTION]
            return out

        @staticmethod
        def all_content():
            return "\n".join(m.content for m in _RecMessage.sent)

    return _Handle


def _no_memory(monkeypatch, app):
    monkeypatch.setattr(app, "recall_validated",
                        lambda *a, **k: [])


def _memory_returns(monkeypatch, app, hints, *, spy=None):
    """Sostituisce SOLO `recall_validated` (il cancello resta l'unica porta)."""
    def _fake(conn, qc, *, role, utente, query=None, limit=5):
        if spy is not None:
            spy.append({"role": role, "utente": utente, "query": query})
        return list(hints)

    monkeypatch.setattr(app, "recall_validated", _fake)
    monkeypatch.setattr(app, "get_pg_conn", lambda: _NullConnCM())
    monkeypatch.setattr(app, "get_qdrant_client", lambda: object())


class _NullConnCM:
    def __enter__(self):
        return object()

    def __exit__(self, *a):
        return False


def _run(coro):
    return asyncio.run(coro)


class _Msg:
    def __init__(self, content):
        self.content = content


class TestLaCardNonAppareSuUnaRispostaBuona:

    def test_su_ALTA_nessuna_card_e_nessun_pulsante(self, ui, monkeypatch):
        app = ui.module
        ui.answer_spy.level = "ALTA"
        _memory_returns(monkeypatch, app, [_hint("prezzo della pompa ABC124")])
        _run(app.on_message(_Msg("quanto costa la pompa?")))
        assert ui.memory_actions() == []
        assert "Suggerimento" not in ui.all_content()

    def test_su_ALTA_la_memoria_non_viene_nemmeno_interrogata(self, ui,
                                                             monkeypatch):
        """Non e' un dettaglio di efficienza: se la memoria non viene
        interrogata, non c'e' nessun percorso lungo il quale un ricordo possa
        finire accanto a una risposta ALTA — nemmeno per un errore a valle."""
        app = ui.module
        ui.answer_spy.level = "ALTA"
        chiamate = []
        monkeypatch.setattr(app, "recall_validated",
                            lambda *a, **k: chiamate.append(1) or [])
        monkeypatch.setattr(app, "get_pg_conn",
                            lambda: chiamate.append("db") or _NullConnCM())
        monkeypatch.setattr(app, "get_qdrant_client", lambda: object())
        _run(app.on_message(_Msg("quanto costa la pompa?")))
        assert chiamate == [], "la memoria e' stata interrogata su una ALTA"

    @pytest.mark.parametrize("level", ["BASSA", "MEDIA"])
    def test_su_BASSA_e_MEDIA_la_card_appare(self, ui, monkeypatch, level):
        app = ui.module
        ui.answer_spy.level = level
        _memory_returns(monkeypatch, app,
                        [_hint("prezzo della pompa idraulica ABC124")])
        _run(app.on_message(_Msg("quanto costa la pompa idraulica?")))
        assert len(ui.memory_actions()) == 1
        assert "prezzo della pompa idraulica ABC124" in ui.all_content()


class TestLaMemoriaNonToccaLaQuery:
    """La suite che avrebbe preso l'exploit dell'11/08, applicata al NUOVO
    consumatore. Prima l'exploit passava dalla riformulazione; oggi l'unico
    posto da cui la memoria potrebbe rientrare e' questo cablaggio."""

    def test_la_query_passata_al_grafo_e_quella_digitata_verbatim(
            self, ui, monkeypatch):
        app = ui.module
        _memory_returns(monkeypatch, app, [
            _hint("il radiatore nella versione piccola V09199300001")])
        _run(app.on_message(_Msg("il carburatore nella versione grande?")))
        assert ui.answer_spy.queries == ["il carburatore nella versione grande?"]

    def test_il_codice_del_ricordo_non_entra_nella_query(self, ui, monkeypatch):
        """L'exploit storico in una riga: domanda sul CARBURATORE, ricordo sul
        RADIATORE con un codice REALE a catalogo. Il codice non deve comparire
        in cio' che il grafo riceve."""
        app = ui.module
        _memory_returns(monkeypatch, app, [
            _hint("il radiatore nella versione piccola V09199300001")])
        _run(app.on_message(_Msg("il carburatore nella versione grande?")))
        for q in ui.answer_spy.queries:
            assert "V09199300001" not in q

    def test_il_ricordo_non_entra_nemmeno_nel_contesto_conversazionale(
            self, ui, monkeypatch):
        """Il contesto e' l'altra porta: se il ricordo finisse fra i turni, al
        giro dopo lo leggerebbe il riformulatore — e l'exploit tornerebbe da
        li'. I turni devono contenere SOLO domanda e risposta."""
        app = ui.module
        _memory_returns(monkeypatch, app, [
            _hint("il radiatore nella versione piccola V09199300001")])
        _run(app.on_message(_Msg("il carburatore nella versione grande?")))
        turns = ui.session_obj.get("conv_turns")
        blob = json.dumps(turns, default=str)
        assert "V09199300001" not in blob
        assert "radiatore" not in blob

    def test_la_memoria_e_interrogata_DOPO_la_risposta(self, ui, monkeypatch):
        """Prova STRUTTURALE, non di comportamento: al momento del recall la
        risposta e' gia' stata calcolata, quindi la memoria non puo' averla
        influenzata. E' l'ordine a rendere l'exploit impossibile, non una
        guardia che qualcuno potrebbe dimenticare di cablare."""
        app = ui.module
        ordine = []
        spy = _AnswerSpy(level="BASSA")

        def _answer(*a, **k):
            ordine.append("answer")
            return spy(*a, **k)

        monkeypatch.setattr(app, "answer", _answer)

        def _recall(conn, qc, *, role, utente, query=None, limit=5):
            ordine.append("recall")
            return [_hint()]

        monkeypatch.setattr(app, "recall_validated", _recall)
        monkeypatch.setattr(app, "get_pg_conn", lambda: _NullConnCM())
        monkeypatch.setattr(app, "get_qdrant_client", lambda: object())
        _run(app.on_message(_Msg("quanto costa la pompa idraulica?")))
        assert ordine == ["answer", "recall"]


class TestIlClickValeUnTurnoUtenteVerbatim:

    def _card_click(self, app, payload):
        class _Action:
            def __init__(self, p):
                self.name = app.MEMORY_ACTION
                self.payload = p
                self.forId = "msg-1"

        return _run(app.on_memory_suggest(_Action(payload)))

    def test_il_click_rifa_la_domanda_suggerita_verbatim(self, ui, monkeypatch):
        app = ui.module
        suggerita = "prezzo della pompa idraulica ABC124"
        _memory_returns(monkeypatch, app, [_hint(suggerita)])
        _run(app.on_message(_Msg("quanto costa la pompa idraulica?")))
        azione = ui.memory_actions()[0]
        self._card_click(app, azione.payload)
        assert ui.answer_spy.queries[-1] == suggerita

    def test_il_click_non_concatena_la_domanda_originale(self, ui, monkeypatch):
        """Un suggerimento CONCATENATO sarebbe la memoria che estende la query
        con un altro nome: e' precisamente cio' che il pivot vieta."""
        app = ui.module
        suggerita = "prezzo della pompa idraulica ABC124"
        _memory_returns(monkeypatch, app, [_hint(suggerita)])
        _run(app.on_message(_Msg("quanto costa il carburatore?")))
        self._card_click(app, ui.memory_actions()[0].payload)
        ultima = ui.answer_spy.queries[-1]
        assert ultima == suggerita
        assert "carburatore" not in ultima

    def test_il_click_produce_un_turno_UTENTE_visibile(self, ui, monkeypatch):
        """L'utente deve VEDERE scritto cio' che ha appena chiesto: e' la
        differenza fra 'la memoria ha deciso per me' e 'l'ho chiesto io'."""
        app = ui.module
        suggerita = "prezzo della pompa idraulica ABC124"
        _memory_returns(monkeypatch, app, [_hint(suggerita)])
        _run(app.on_message(_Msg("quanto costa la pompa idraulica?")))
        prima = len(ui.messages())
        self._card_click(app, ui.memory_actions()[0].payload)
        nuovi = ui.messages()[prima:]
        echo = [m for m in nuovi if m.type == "user_message"]
        assert len(echo) == 1
        assert echo[0].content == suggerita

    def test_il_click_finisce_nella_cronologia_come_turno_utente(
            self, ui, monkeypatch):
        app = ui.module
        suggerita = "prezzo della pompa idraulica ABC124"
        _memory_returns(monkeypatch, app, [_hint(suggerita)])
        _run(app.on_message(_Msg("quanto costa la pompa idraulica?")))
        self._card_click(app, ui.memory_actions()[0].payload)
        turns = ui.session_obj.get("conv_turns")
        assert {"role": "user", "content": suggerita} in [
            {"role": t["role"], "content": t["content"]} for t in turns]

    def test_il_click_passa_dalle_stesse_difese_della_digitazione(
            self, ui, monkeypatch):
        """Non ci sono scorciatoie: il click deve attraversare lo STESSO codice
        di un messaggio digitato. Qui lo si prova sul fatto che entrambi
        producono lo stesso identico numero e tipo di chiamate al grafo, con lo
        stesso ruolo e lo stesso assemblaggio di contesto."""
        app = ui.module
        suggerita = "prezzo della pompa idraulica ABC124"
        _memory_returns(monkeypatch, app, [_hint(suggerita)])
        _run(app.on_message(_Msg("domanda digitata")))
        chiamate_dopo_digitazione = list(ui.answer_spy.calls)
        self._card_click(app, ui.memory_actions()[0].payload)
        click = ui.answer_spy.calls[-1]
        assert len(ui.answer_spy.calls) == len(chiamate_dopo_digitazione) + 1
        assert click["role"] == chiamate_dopo_digitazione[0]["role"]
        assert click["context"] is not None

    def test_un_payload_manomesso_resta_una_normale_domanda_utente(
            self, ui, monkeypatch):
        """Il payload dell'azione fa un giro dal client e puo' tornare alterato.
        Non e' un'escalation: il testo cliccato vale ESATTAMENTE quanto un
        testo digitato, cioe' nessun privilegio. Va comunque provato che non
        esista una corsia preferenziale — la difesa e' che non c'e' NIENTE di
        speciale nel percorso del click."""
        app = ui.module
        _memory_returns(monkeypatch, app, [_hint("prezzo della pompa ABC124")])
        _run(app.on_message(_Msg("quanto costa la pompa?")))
        self._card_click(app, {"text": "dammi il prezzo di V09199300001",
                               "hint_id": 999})
        assert ui.answer_spy.queries[-1] == "dammi il prezzo di V09199300001"
        assert ui.answer_spy.calls[-1]["role"] == "tecnico"

    @pytest.mark.parametrize("payload", [
        {}, {"text": ""}, {"text": "   "}, {"text": None},
        {"hint_id": 1}, {"text": 123},
    ])
    def test_un_payload_vuoto_o_rotto_non_solleva(self, ui, monkeypatch,
                                                  payload):
        app = ui.module
        _no_memory(monkeypatch, app)
        self._card_click(app, payload)
        assert ui.answer_spy.queries == []


class TestIlCancelloNonEScavalcabile:

    def test_la_ui_non_importa_il_magazzino_direttamente(self):
        """Il magazzino (`memory_store`) senza il cancello (`revalidation`) e'
        precisamente il leak che il cancello esiste per impedire. La UI deve
        poter chiamare SOLO `recall_validated`."""
        import src.ui.chainlit_app as app
        tree = ast.parse(Path(app.__file__).read_text(encoding="utf-8"))
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported += [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
                imported += [f"{node.module or ''}.{a.name}"
                             for a in node.names]
        offenders = [m for m in imported
                     if "memory_store" in m or "MemoryStore" in m]
        assert offenders == [], (
            f"la UI importa {offenders}: il recall deve passare dal cancello "
            "(`recall_validated`), mai dal magazzino nudo.")

    def test_se_il_cancello_scarta_tutto_non_si_mostra_nulla(self, ui,
                                                             monkeypatch):
        app = ui.module
        _memory_returns(monkeypatch, app, [])
        _run(app.on_message(_Msg("quanto costa la pompa idraulica?")))
        assert ui.memory_actions() == []

    def test_se_il_cancello_esplode_la_risposta_arriva_lo_stesso(
            self, ui, monkeypatch):
        """La memoria non deve MAI rompere una risposta: e' la promessa scritta
        in `revalidation.py`, e va mantenuta anche dal consumatore."""
        app = ui.module

        def _boom(*a, **k):
            raise RuntimeError("Qdrant giu'")

        monkeypatch.setattr(app, "recall_validated", _boom)
        monkeypatch.setattr(app, "get_pg_conn", lambda: _NullConnCM())
        monkeypatch.setattr(app, "get_qdrant_client", lambda: object())
        _run(app.on_message(_Msg("quanto costa la pompa idraulica?")))
        assert "non lo so" in ui.all_content()
        assert ui.memory_actions() == []

    def test_se_il_DB_e_giu_la_risposta_arriva_lo_stesso(self, ui, monkeypatch):
        app = ui.module

        def _boom():
            raise RuntimeError("Postgres giu'")

        monkeypatch.setattr(app, "get_pg_conn", _boom)
        monkeypatch.setattr(app, "get_qdrant_client", lambda: object())
        _run(app.on_message(_Msg("quanto costa la pompa idraulica?")))
        assert "non lo so" in ui.all_content()
        assert ui.memory_actions() == []

    def test_il_recall_riceve_il_ruolo_di_SESSIONE_tipizzato(self, ui,
                                                            monkeypatch):
        """`recall_validated` solleva `TypeError` su un ruolo che non sia
        `Role`: la UI deve passargli il ruolo della SESSIONE, gia' validato,
        mai la stringa grezza e mai il ruolo di chi confermo' il binding."""
        app = ui.module
        visto = []
        _memory_returns(monkeypatch, app, [_hint()], spy=visto)
        _run(app.on_message(_Msg("quanto costa la pompa idraulica?")))
        assert visto and visto[0]["role"] is Role.TECNICO
        assert visto[0]["utente"] == "bob"

    def test_un_ruolo_di_sessione_illeggibile_non_mostra_nulla(self, ui,
                                                              monkeypatch):
        app = ui.module
        ui.session_obj.set("user", _FakeUser(role="ruolo_inventato"))
        _memory_returns(monkeypatch, app, [_hint()])
        _run(app.on_message(_Msg("quanto costa la pompa idraulica?")))
        assert ui.memory_actions() == []


class TestAntiDeriva:

    def test_cl_Message_accetta_ancora_il_tipo_user_message(self):
        """Il click vale come turno UTENTE: se Chainlit togliesse questo tipo,
        l'eco del click tornerebbe a sembrare un messaggio dell'assistente e la
        card avrebbe di nuovo l'aria di una risposta."""
        import chainlit as cl
        ann = inspect.signature(cl.Message.__init__).parameters["type"]
        assert "user_message" in str(ann.annotation)

    def test_esiste_un_solo_punto_di_ingresso_per_una_domanda(self):
        """`on_message` e `on_memory_suggest` devono confluire nella STESSA
        funzione: due percorsi paralleli sono due insiemi di difese da tenere
        allineati a mano, ed e' cosi' che nascono le guardie morte."""
        import src.ui.chainlit_app as app
        tree = ast.parse(Path(app.__file__).read_text(encoding="utf-8"))
        chiamanti = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for sub in ast.walk(node):
                    if (isinstance(sub, ast.Call)
                            and isinstance(sub.func, ast.Name)
                            and sub.func.id == app.handle_question.__name__):
                        chiamanti.add(node.name)
        assert {"on_message", "on_memory_suggest"} <= chiamanti, chiamanti


# =============================================================================
# PARTE C — integrazione Postgres + Qdrant REALI (con ROLLBACK)
# =============================================================================
#
# Documenti REALI del corpus (gli stessi verificati in tests/test_revalidation.py):
#   flow_exploded_2022             -> ruoli_consentiti ['tecnico']
#   pi_pulley_listino_pulegge_2025 -> visibile anche al commerciale
# Entrambi 'current', ingested_at 2026-07-25.

DOC_SOLO_TECNICO = "flow_exploded_2022"
DOC_ANCHE_COMMERCIALE = "pi_pulley_listino_pulegge_2025"
SEGRETO = "SEGRETO_TESTO_DEL_BINDING_CHE_NON_DEVE_APPARIRE"


def _get_conn():
    try:
        from src.backend.clients.postgres_client import get_pg_conn
        return get_pg_conn()
    except Exception as exc:
        pytest.skip(f"Postgres non raggiungibile: {exc}")


def _get_qc():
    try:
        from src.backend.clients.qdrant_client import get_qdrant_client
        qc = get_qdrant_client()
        qc.get_collections()
        return qc
    except Exception as exc:
        pytest.skip(f"Qdrant non raggiungibile: {exc}")


def _insert_feedback(conn, **kw) -> int:
    return conn.execute(
        """
        INSERT INTO public.feedback (
            utente, ruolo, query, risposta, citazioni, verdetto,
            livello_memoria, stato_moderazione, conferme_count, created_at)
        VALUES (%(utente)s, %(ruolo)s, %(query)s, %(risposta)s, %(citazioni)s,
                'conferma', %(livello_memoria)s, %(stato_moderazione)s, 3,
                %(created_at)s)
        RETURNING id
        """,
        {"utente": kw["utente"], "ruolo": kw["ruolo"], "query": kw["query"],
         "risposta": kw.get("risposta", SEGRETO),
         "citazioni": json.dumps(kw["citazioni"]),
         "livello_memoria": kw.get("livello_memoria", "aziendale"),
         "stato_moderazione": kw.get("stato_moderazione", "approved"),
         "created_at": kw.get("created_at", datetime.now(UTC))},
    ).fetchone()[0]


class _SharedConnCM:
    """Presta alla UI la MIA transazione, cosi' il cancello vero legge le righe
    non ancora committate e il rollback finale pulisce tutto."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *a):
        return False


@pytest.fixture
def ui_reale(ui, monkeypatch):
    """La UI reale col cancello REALE su Postgres e Qdrant veri."""
    conn = _get_conn()
    qc = _get_qc()
    app = ui.module
    monkeypatch.setattr(app, "get_pg_conn", lambda: _SharedConnCM(conn))
    monkeypatch.setattr(app, "get_qdrant_client", lambda: qc)
    try:
        yield ui, conn
    finally:
        conn.rollback()
        conn.close()


def _set_role(ui, role):
    ui.session_obj.set("user", _FakeUser(identifier="bob", role=role))


class TestIntegrazioneIlCancelloDalVivo:

    def test_INTEGRAZIONE_un_ricordo_di_un_altro_ruolo_non_trapela(
            self, ui_reale):
        """IL test RBAC dell'epica, dal lato dello SCHERMO.

        Un binding `aziendale`+`approved` supera il pre-filtro del magazzino
        (che filtra la RIGA DI FEEDBACK, non l'EVIDENZA) ma cita un documento
        riservato ai tecnici. Il suo testo non deve comparire nella card di un
        commerciale. Controprova col tecnico nello stesso test: se la card non
        comparisse MAI, questo test sarebbe verde per il motivo sbagliato.
        """
        ui, conn = ui_reale
        app = ui.module
        marker = "MARKUICARD pompa idraulica del flow exploded"
        _insert_feedback(conn, utente="mallory", ruolo="tecnico", query=marker,
                         citazioni=[{"doc": DOC_SOLO_TECNICO, "page": 7}])

        _set_role(ui, "commerciale")
        _run(app.on_message(_Msg("pompa idraulica flow exploded")))
        schermo = ui.all_content()
        assert marker not in schermo
        assert SEGRETO not in schermo
        assert ui.memory_actions() == []

        _RecMessage.sent.clear()
        _set_role(ui, "tecnico")
        ui.session_obj.set("conv_turns", [])
        _run(app.on_message(_Msg("pompa idraulica flow exploded")))
        assert marker in ui.all_content(), (
            "controprova fallita: la card non appare nemmeno al tecnico, "
            "quindi il test sopra non prova nulla")
        assert len(ui.memory_actions()) == 1

    def test_INTEGRAZIONE_un_ricordo_stale_non_viene_mostrato(self, ui_reale):
        """Il documento e' stato re-ingerito DOPO la conferma: quel ricordo
        parla di una riga che potrebbe non esistere piu'. Meglio non suggerire
        nulla che suggerire una cosa superata."""
        ui, conn = ui_reale
        app = ui.module
        _set_role(ui, "tecnico")
        marker = "MARKUISTALE pompa idraulica del flow exploded vecchia"
        _insert_feedback(conn, utente="bob", ruolo="tecnico", query=marker,
                         citazioni=[{"doc": DOC_SOLO_TECNICO, "page": 7}],
                         created_at=datetime(2026, 1, 1, tzinfo=UTC))
        _run(app.on_message(_Msg("pompa idraulica flow exploded vecchia")))
        assert marker not in ui.all_content()
        assert ui.memory_actions() == []

    def test_INTEGRAZIONE_un_ricordo_su_un_documento_fantasma_non_appare(
            self, ui_reale):
        ui, conn = ui_reale
        app = ui.module
        _set_role(ui, "tecnico")
        marker = "MARKUIGHOST pompa idraulica di un documento fantasma"
        _insert_feedback(conn, utente="bob", ruolo="tecnico", query=marker,
                         citazioni=[{"doc": "documento_mai_esistito_zzz",
                                     "page": 1}])
        _run(app.on_message(_Msg("pompa idraulica documento fantasma")))
        assert marker not in ui.all_content()

    def test_INTEGRAZIONE_caso_felice_la_card_arriva_ed_e_cliccabile(
            self, ui_reale):
        """Il test che impedisce a tutti gli altri di essere verdi per nulla:
        una memoria legittima DEVE arrivare a schermo e il suo click DEVE
        rifare la domanda verbatim."""
        ui, conn = ui_reale
        app = ui.module
        _set_role(ui, "commerciale")
        marker = "MARKUIOK listino pulegge per il commerciale"
        _insert_feedback(conn, utente="bob", ruolo="commerciale", query=marker,
                         citazioni=[{"doc": DOC_ANCHE_COMMERCIALE, "page": 3}])
        _run(app.on_message(_Msg("listino pulegge")))
        assert marker in ui.all_content()
        azioni = ui.memory_actions()
        assert len(azioni) == 1

        class _Action:
            name = app.MEMORY_ACTION
            payload = azioni[0].payload
            forId = "msg-1"

        _run(app.on_memory_suggest(_Action()))
        assert ui.answer_spy.queries[-1] == marker

    def test_INTEGRAZIONE_su_ALTA_nemmeno_una_memoria_valida_appare(
            self, ui_reale):
        ui, conn = ui_reale
        app = ui.module
        ui.answer_spy.level = "ALTA"
        _set_role(ui, "commerciale")
        marker = "MARKUIALTA listino pulegge per il commerciale"
        _insert_feedback(conn, utente="bob", ruolo="commerciale", query=marker,
                         citazioni=[{"doc": DOC_ANCHE_COMMERCIALE, "page": 3}])
        _run(app.on_message(_Msg("listino pulegge")))
        assert marker not in ui.all_content()
        assert ui.memory_actions() == []

    def test_INTEGRAZIONE_il_rollback_non_lascia_righe(self):
        """Guardia sul metodo: se i test sopra sporcassero il DB di produzione
        il prossimo giro misurerebbe un mondo diverso."""
        conn = _get_conn()
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM public.feedback "
                "WHERE query LIKE 'MARKUI%'").fetchone()[0]
            assert n == 0, f"{n} righe MARKUI* sopravvissute al rollback"
        finally:
            conn.rollback()
            conn.close()
