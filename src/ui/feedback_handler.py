import logging
from typing import Any

from src.backend.clients.postgres_client import get_pg_conn
from src.registry.feedback import init_schema, insert_feedback

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
    correzione: str | None = None,
    provenienza: str = "corpus"
) -> int | None:
    """Wrapper UI per il salvataggio del feedback.

    Apre e chiude la connessione e cattura tutte le eccezioni (logging warning).
    Non crasha mai.

    `provenienza` (Epica H): 'corpus' | 'web' | 'misto' | 'nessuna', calcolata
    dalla UI in base alla forma delle citazioni (vedi chainlit_app.py). Deve
    finire nel record: la promozione feedback->memoria (F.2b, futura) non deve
    poter perdere per strada che una risposta viene da internet.
    """
    citazioni_dicts = None
    if citazioni:
        # Epica H: da quando la provenienza puo' essere 'web', `citazioni` non
        # e' piu' garantito essere SOLO coppie (doc, page) — puo' contenere
        # anche URL (stringa) o mapping con 'url' (vedi classify_citations in
        # chainlit_app.py). Lo spacchettamento cieco `for doc, page in ...`
        # romperebbe su una stringa (troppi valori da spacchettare). Qui si
        # normalizza voce per voce, tollerante alla forma.
        citazioni_dicts = []
        for c in citazioni:
            if isinstance(c, str):
                citazioni_dicts.append({"url": c})
            elif isinstance(c, dict):
                citazioni_dicts.append(c)
            else:
                doc, page = c
                citazioni_dicts.append({"doc": doc, "page": page})

    try:
        with get_pg_conn() as conn:
            # Migrazione idempotente PRIMA dell'insert. Non e' pignoleria: la
            # colonna `provenienza` (03f385d) era applicata SOLO da
            # `tests/test_feedback.py`, che si costruisce lo schema da se'. Sul
            # DB di questa macchina esiste perche' e' stata applicata a mano;
            # su qualunque altro ambiente il primo feedback sarebbe esploso.
            # E' alla lettera la lezione dell'epica D gia' pagata una volta:
            # un test su schema-test NON prova la produzione. `init_schema` e'
            # idempotente (IF NOT EXISTS ovunque) e questa e' l'unica porta di
            # scrittura del feedback, quindi e' il posto giusto.
            init_schema(conn)
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
                correzione=correzione,
                provenienza=provenienza
            )
    except Exception as e:
        logger.warning("Errore DB durante il salvataggio del feedback: %s", e, exc_info=True)
        return None
