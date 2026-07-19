import logging
from typing import Any

from src.backend.clients.postgres_client import get_pg_conn
from src.registry.feedback import insert_feedback

logger = logging.getLogger(__name__)

def save_feedback_ui(
    utente: str,
    ruolo: str,
    query: str,
    risposta: str,
    final_level: str | None,
    citazioni: list[tuple[str, int]] | None,
    contesto: dict[str, Any] | None,
    verdetto: str,
    correzione: str | None = None
) -> int | None:
    """Wrapper UI per il salvataggio del feedback.
    
    Apre e chiude la connessione e cattura tutte le eccezioni (logging warning).
    Non crasha mai.
    """
    citazioni_dicts = None
    if citazioni:
        citazioni_dicts = [{"doc": doc, "page": page} for doc, page in citazioni]
        
    try:
        with get_pg_conn() as conn:
            return insert_feedback(
                conn,
                utente=utente,
                ruolo=ruolo,
                query=query,
                risposta=risposta,
                final_level=final_level,
                citazioni=citazioni_dicts,
                contesto=contesto,
                verdetto=verdetto,
                correzione=correzione
            )
    except Exception as e:
        logger.warning("Errore DB durante il salvataggio del feedback: %s", e, exc_info=True)
        return None
