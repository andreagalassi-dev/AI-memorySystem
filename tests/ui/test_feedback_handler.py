import logging

from src.backend.clients.postgres_client import get_pg_conn
from src.ui.feedback_handler import save_feedback_ui

def test_save_feedback_ui_success():
    """Testa l'inserimento con DB vero e rilettura."""
    utente_test = "test-ui-user-123"
    
    # Inserimento
    fb_id = save_feedback_ui(
        utente=utente_test,
        ruolo="tecnico",
        query="qual è il prezzo?",
        risposta="100 euro",
        final_level="ALTA",
        citazioni=[("doc1", 10)],
        contesto={
            "audit": ["nodo1", "nodo2"],
            "evidence": [
                {"doc": "doc1", "page": 10, "code": "A1", "score": 0.95, "text": "testo " * 100}
            ]
        },
        verdetto="conferma"
    )
    
    assert fb_id is not None
    
    # Rilettura
    with get_pg_conn() as conn:
        row = conn.execute("SELECT * FROM feedback WHERE id = %s", (fb_id,)).fetchone()
        assert row is not None
        
        # Cleanup
        conn.execute("DELETE FROM feedback WHERE id = %s", (fb_id,))
        conn.commit()


def test_save_feedback_ui_error(monkeypatch, caplog):
    """Testa il monkeypatch della connessione: nessuna eccezione, ma warning in log."""
    
    def mock_get_conn():
        raise RuntimeError("Connessione persa simulata")
        
    monkeypatch.setattr("src.ui.feedback_handler.get_pg_conn", mock_get_conn)
    
    with caplog.at_level(logging.WARNING):
        fb_id = save_feedback_ui(
            utente="test",
            ruolo="tecnico",
            query="q",
            risposta="r",
            final_level=None,
            citazioni=None,
            contesto=None,
            verdetto="segnala"
        )
        
    assert fb_id is None
    assert "Errore DB durante il salvataggio del feedback" in caplog.text
    assert "Connessione persa simulata" in caplog.text


def test_save_feedback_ui_provenienza_web(monkeypatch):
    """Epica H: la provenienza calcolata dalla UI deve arrivare intatta a
    insert_feedback (record, non solo grafica) — vedi chainlit_app.py."""
    captured = {}

    def fake_insert_feedback(conn, **kwargs):
        captured.update(kwargs)
        return 42

    class FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr("src.ui.feedback_handler.get_pg_conn", lambda: FakeConn())
    monkeypatch.setattr("src.ui.feedback_handler.insert_feedback", fake_insert_feedback)
    # la migrazione idempotente e' parte del percorso di scrittura: la si
    # neutralizza qui perche' la connessione e' finta, non perche' sia
    # opzionale (senza, un ambiente nuovo non avrebbe la colonna provenienza)
    monkeypatch.setattr("src.ui.feedback_handler.init_schema", lambda conn: None)

    fb_id = save_feedback_ui(
        utente="test",
        ruolo="tecnico",
        query="q",
        risposta="r (dal web)",
        final_level="WEB",
        citazioni=None,
        contesto=None,
        verdetto="conferma",
        provenienza="web",
    )

    assert fb_id == 42
    assert captured["provenienza"] == "web"


def test_save_feedback_ui_citazioni_miste_corpus_e_web(monkeypatch):
    """Epica H: 'citazioni' puo' contenere sia coppie (doc,page) sia URL web
    (stringa) — lo spacchettamento cieco romperebbe su una stringa. Verifica
    che entrambe le forme sopravvivano nel record, senza eccezioni."""
    captured = {}

    def fake_insert_feedback(conn, **kwargs):
        captured.update(kwargs)
        return 7

    class FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr("src.ui.feedback_handler.get_pg_conn", lambda: FakeConn())
    monkeypatch.setattr("src.ui.feedback_handler.insert_feedback", fake_insert_feedback)
    # la migrazione idempotente e' parte del percorso di scrittura: la si
    # neutralizza qui perche' la connessione e' finta, non perche' sia
    # opzionale (senza, un ambiente nuovo non avrebbe la colonna provenienza)
    monkeypatch.setattr("src.ui.feedback_handler.init_schema", lambda conn: None)

    fb_id = save_feedback_ui(
        utente="test",
        ruolo="tecnico",
        query="q",
        risposta="r",
        final_level="WEB",
        citazioni=[("doc1", 3), "https://example.com/pezzo"],
        contesto=None,
        verdetto="conferma",
        provenienza="misto",
    )

    assert fb_id == 7
    assert captured["citazioni"] == [
        {"doc": "doc1", "page": 3},
        {"url": "https://example.com/pezzo"},
    ]


def test_save_feedback_ui_provenienza_default_corpus(monkeypatch):
    """Chi non specifica provenienza (chiamanti pre-esistenti) resta 'corpus'."""
    captured = {}

    def fake_insert_feedback(conn, **kwargs):
        captured.update(kwargs)
        return 1

    class FakeConn:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    monkeypatch.setattr("src.ui.feedback_handler.get_pg_conn", lambda: FakeConn())
    monkeypatch.setattr("src.ui.feedback_handler.insert_feedback", fake_insert_feedback)
    # la migrazione idempotente e' parte del percorso di scrittura: la si
    # neutralizza qui perche' la connessione e' finta, non perche' sia
    # opzionale (senza, un ambiente nuovo non avrebbe la colonna provenienza)
    monkeypatch.setattr("src.ui.feedback_handler.init_schema", lambda conn: None)

    save_feedback_ui(
        utente="test",
        ruolo="tecnico",
        query="q",
        risposta="r",
        final_level="ALTA",
        citazioni=None,
        contesto=None,
        verdetto="conferma",
    )

    assert captured["provenienza"] == "corpus"
