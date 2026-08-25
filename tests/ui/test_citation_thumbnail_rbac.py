"""Le miniature delle citazioni: seconda serratura sulla stessa porta.

NON e' una falla oggi, e va detto per primo. Le citazioni arrivano da
`answer()`, che filtra per ruolo su tutti e tre i rami (structured dopo 1083d71,
retrieval via `access_filter`, visual via `visual_search`). Il ciclo di
`handle_question` che chiama `ensure_page_render(doc, page)` e' pero' l'unico
ALTRO punto della UI che materializza i pixel di un documento senza una seconda
verifica — `on_paginate` e `serve_ui_element` l'hanno presa in adc8449 — e
`ensure_page_render` risolve il PDF con un glob sul filesystem: nessuna query,
nessun ruolo. Se un ramo di retrieval regredisse, la miniatura passerebbe.

Questa e' difesa in profondita', e il modo in cui e' cablata dice cosa e':

  - se la guardia nega, sparisce la MINIATURA, non la risposta. Il testo e' gia'
    filtrato e legittimo, e cancellarlo trasformerebbe una difesa di riserva in
    un guasto;
  - spariscono con la miniatura anche la riga di citazione e i pulsanti di
    paginazione. La riga nominerebbe il documento (un oracolo di esistenza,
    cioe' la falla di `catalog.py`), i pulsanti riaprirebbero la porta chiusa
    in adc8449;
  - e finisce in un log di WARNING. Se scatta, non e' un caso normale: e' il
    segnale che un ramo di retrieval ha regredito, e senza il log si
    perderebbe in silenzio — che e' il difetto che questo progetto continua a
    trovare in se stesso.

Costo, misurato e non supposto (25/08): `doc_visible_to` costa 8.9 ms a
chiamata. Le citazioni per risposta, contate sui 48 run reali di
`output/audit/b2_2_gate_run.json`: 13 volte 0, 27 volte 1, 7 volte 3, una volta
**32**. I DOCUMENTI distinti per risposta pero' sono 1 in 34 casi su 35 e 3
nell'unico rimanente. La visibilita' e' una proprieta' del DOCUMENTO, non della
pagina: la verifica e' raggruppata per doc_stem e memorizzata per la durata
della risposta, quindi il caso reale costa UNA query e il caso peggiore
possibile (il ciclo si ferma a 8 pagine distinte) ne costa 8.

Disciplina dei test: `ensure_page_render` e' un finto che REGISTRA (come in
`tests/ui/test_paginate_rbac.py`), cosi' "niente miniatura" e' una misura e non
un'intenzione; la visibilita' tocca Postgres davvero dove il punto e' l'RBAC.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from src.registry.access import doc_visible_to
from src.ui import chainlit_app
from tests.conftest import needs_pg

# Documento REALE visibile al solo `tecnico` (lo stesso della prima falla RBAC).
TECNICO_ONLY = "Mamoli_Catalogo_Ricambi_2021_IT_EN"
# Documento REALE visibile anche al `commerciale`: senza questo, un test verde
# proverebbe solo che la guardia blocca tutto.
COMMERCIALE_OK = "trumpf_bending_brochure_EN"

RISPOSTA = "Il codice 14576 costa 92.00 EUR."


class _Sessione:
    def __init__(self, dati: dict):
        self._dati = dict(dati)

    def get(self, chiave, default=None):
        return self._dati.get(chiave, default)

    def set(self, chiave, valore):
        self._dati[chiave] = valore


class _Utente:
    def __init__(self, ruolo):
        self.identifier = "utente-di-test"
        self.metadata = {"role": ruolo} if ruolo is not None else {}


@pytest.fixture()
def ui(monkeypatch, tmp_path):
    """Sostituisce Chainlit e il grafo, e REGISTRA cosa e' stato renderizzato.

    `answer` e' finto per due motivi, non uno: il vincolo di non caricare
    modelli, e il fatto che il punto del test e' proprio cosa succede quando
    `answer()` restituisce una citazione che NON avrebbe dovuto restituire.
    """
    stato = SimpleNamespace(messaggi=[], render=[], visibilita=[])

    class _Messaggio:
        _n = 0

        def __init__(self, content="", elements=None, actions=None, **_):
            _Messaggio._n += 1
            self.id = f"msg-{_Messaggio._n}"
            self.content = content
            self.elements = list(elements or [])
            self.actions = list(actions or [])
            self.parent_id = None

        async def send(self):
            stato.messaggi.append(self)
            return self

        async def remove_actions(self):
            return None

    def _render_finto(doc, page):
        stato.render.append((doc, page))
        png = tmp_path / f"{doc}_{page}.png"
        png.write_text("finti pixel")
        return png

    monkeypatch.setattr(chainlit_app, "ensure_page_render", _render_finto)
    monkeypatch.setattr(chainlit_app, "get_deps", lambda ruolo: None)
    monkeypatch.setattr(chainlit_app, "memory_suggestions",
                        lambda *a, **kw: [])
    monkeypatch.setattr(chainlit_app.cl, "Message", _Messaggio)
    monkeypatch.setattr(chainlit_app.cl, "Image",
                        lambda **kw: SimpleNamespace(tipo="image", **kw))
    monkeypatch.setattr(chainlit_app.cl, "Action",
                        lambda **kw: SimpleNamespace(tipo="action", **kw))

    # La guardia vera resta quella importata: qui si intercetta solo per
    # CONTARE le chiamate, delegando la risposta a `doc_visible_to` reale.
    def _spia_visibilita(doc, ruolo):
        stato.visibilita.append((doc, ruolo))
        return doc_visible_to(doc, ruolo)

    monkeypatch.setattr(chainlit_app, "doc_visible_to", _spia_visibilita)

    def _domanda(ruolo, citazioni, *, con_utente=True):
        sessione = _Sessione({"user": _Utente(ruolo)} if con_utente else {})
        monkeypatch.setattr(chainlit_app.cl, "user_session", sessione)
        monkeypatch.setattr(chainlit_app, "answer", lambda *a, **kw: {
            "final_answer": RISPOSTA, "final_level": "ALTA",
            "citations": list(citazioni), "evidence": [], "audit": []})
        asyncio.run(chainlit_app.handle_question("quanto costa il 14576?"))
        return stato.messaggi[-1]

    stato.domanda = _domanda
    return stato


def _nomi_miniature(msg) -> list[str]:
    return [getattr(e, "name", None) for e in msg.elements]


# ------------------------------------------------------- il caso autorizzato


@needs_pg
class TestRuoloAutorizzato:
    """Il rovescio necessario: la demo gira come `tecnico`, che vede 147
    documenti su 147. Il suo comportamento non deve cambiare di una virgola."""

    def test_la_miniatura_c_e(self, ui):
        msg = ui.domanda("tecnico", [(TECNICO_ONLY, 224)])
        assert ui.render == [(TECNICO_ONLY, 224)]
        assert _nomi_miniature(msg) == [f"{TECNICO_ONLY} p.224"]

    def test_la_riga_di_citazione_c_e(self, ui):
        msg = ui.domanda("tecnico", [(TECNICO_ONLY, 224)])
        assert f"Principale: {TECNICO_ONLY} p.224" in msg.content

    def test_i_pulsanti_di_paginazione_ci_sono(self, ui):
        msg = ui.domanda("tecnico", [(TECNICO_ONLY, 224)])
        paginate = [a for a in msg.actions if getattr(a, "name", "") == "paginate"]
        assert len(paginate) == 2

    def test_nessun_warning_nel_caso_normale(self, ui, caplog):
        with caplog.at_level(logging.WARNING, logger=chainlit_app.__name__):
            ui.domanda("tecnico", [(TECNICO_ONLY, 224)])
        assert [r for r in caplog.records if "citazione" in r.getMessage()] == []

    def test_piu_pagine_dello_stesso_documento_rendono_tutte(self, ui):
        ui.domanda("tecnico", [(TECNICO_ONLY, 224), (TECNICO_ONLY, 225)])
        assert ui.render == [(TECNICO_ONLY, 224), (TECNICO_ONLY, 225)]


# ------------------------------------------------------------ il caso negato


@needs_pg
class TestRuoloNonAutorizzato:
    """Il ramo che oggi non si raggiunge: `answer()` non consegnerebbe mai
    questa citazione a un commerciale. Il test la consegna a forza, che e'
    l'unico modo di provare che la seconda serratura esiste."""

    def test_la_miniatura_sparisce(self, ui):
        msg = ui.domanda("commerciale", [(TECNICO_ONLY, 224)])
        assert ui.render == [], (
            "i pixel di un documento non visibile al ruolo sono stati prodotti")
        assert msg.elements == []

    def test_la_risposta_testuale_resta(self, ui):
        """Il punto dell'intera correzione: e' la miniatura a sparire, non la
        risposta. Il testo arriva da `answer()`, che il ruolo lo ha applicato."""
        msg = ui.domanda("commerciale", [(TECNICO_ONLY, 224)])
        assert RISPOSTA in msg.content
        assert "Errore durante l'elaborazione" not in msg.content

    def test_il_documento_non_viene_nominato(self, ui):
        """Una riga "Vedi: <doc> p.224" sarebbe un oracolo di esistenza — la
        falla chiusa in `catalog.py` lo stesso giorno."""
        msg = ui.domanda("commerciale", [(TECNICO_ONLY, 224)])
        assert TECNICO_ONLY not in msg.content

    def test_niente_pulsanti_di_paginazione(self, ui):
        """Altrimenti il rifiuto sarebbe un passo indietro e basta: il click
        successivo ripartirebbe da `on_paginate`."""
        msg = ui.domanda("commerciale", [(TECNICO_ONLY, 224)])
        assert [a for a in msg.actions if getattr(a, "name", "") == "paginate"] == []

    def test_i_pulsanti_di_feedback_restano(self, ui):
        msg = ui.domanda("commerciale", [(TECNICO_ONLY, 224)])
        assert [a for a in msg.actions if getattr(a, "name", "") == "feedback"]

    def test_emette_un_warning(self, ui, caplog):
        """Se scatta, un ramo di retrieval ha regredito: e' un segnale, non un
        caso normale, e senza log si perderebbe in silenzio."""
        with caplog.at_level(logging.WARNING, logger=chainlit_app.__name__):
            ui.domanda("commerciale", [(TECNICO_ONLY, 224)])
        avvisi = [r.getMessage() for r in caplog.records
                  if r.levelno >= logging.WARNING]
        assert any(TECNICO_ONLY in m for m in avvisi), avvisi

    def test_il_documento_permesso_rende_ancora_nella_stessa_risposta(self, ui):
        """Una citazione negata non deve trascinarsi dietro le altre."""
        msg = ui.domanda("commerciale",
                         [(TECNICO_ONLY, 224), (COMMERCIALE_OK, 2)])
        assert ui.render == [(COMMERCIALE_OK, 2)]
        assert _nomi_miniature(msg) == [f"{COMMERCIALE_OK} p.2"]
        assert RISPOSTA in msg.content


@needs_pg
class TestSessioneSenzaRuolo:
    def test_utente_senza_ruolo_non_ottiene_miniature(self, ui):
        msg = ui.domanda(None, [(TECNICO_ONLY, 224)])
        assert ui.render == []
        assert RISPOSTA in msg.content


# ------------------------------------------------------------ il costo


@needs_pg
class TestRaggruppamento:
    """Il costo va misurato, non supposto: la visibilita' e' una proprieta' del
    DOCUMENTO, quindi si chiede una volta per doc_stem e non una per miniatura.
    Sui 48 run reali del gate il massimo di citazioni per risposta e' 32, ma i
    documenti distinti sono al massimo 3."""

    def test_tre_pagine_dello_stesso_documento_costano_una_query(self, ui):
        ui.domanda("tecnico", [(TECNICO_ONLY, 224), (TECNICO_ONLY, 225),
                               (TECNICO_ONLY, 226)])
        assert ui.visibilita == [(TECNICO_ONLY, "tecnico")]
        assert len(ui.render) == 3

    def test_due_documenti_costano_due_query(self, ui):
        ui.domanda("tecnico", [(TECNICO_ONLY, 1), (COMMERCIALE_OK, 1),
                               (TECNICO_ONLY, 2)])
        assert ui.visibilita == [(TECNICO_ONLY, "tecnico"),
                                 (COMMERCIALE_OK, "tecnico")]

    def test_il_tetto_di_otto_pagine_limita_anche_le_query(self, ui):
        """Il ciclo si ferma a 8 pagine distinte: e' quello il caso peggiore,
        8 query da ~9 ms, non una per citazione."""
        citazioni = [(f"doc_inesistente_{i}", 1) for i in range(30)]
        ui.domanda("tecnico", citazioni)
        assert len(ui.visibilita) == 8


# ------------------------------------------- il fatto che regge la demo


@needs_pg
def test_per_il_tecnico_tutti_i_documenti_restano_visibili():
    """La prova che il comportamento della demo non cambia: la guardia e' un
    no-op MISURATO per il ruolo con cui gira la demo, non supposto."""
    import psycopg

    from src.backend.clients.postgres_client import conninfo_readonly

    with psycopg.connect(conninfo_readonly()) as conn:
        slug = [r[0] for r in
                conn.execute("SELECT slug FROM public.documents").fetchall()]
    assert len(slug) == 147
    assert sum(1 for s in slug if doc_visible_to(s, "tecnico")) == len(slug)
