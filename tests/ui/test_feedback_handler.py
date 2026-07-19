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
