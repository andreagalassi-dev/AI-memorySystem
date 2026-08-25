"""Un DOCUMENTO e' visibile a un RUOLO? (SPEC §F.3) — guardia riusabile.

Perche' qui e non in `src/ui/`: le tabelle interrogate (`public.documents`,
`public.document_roles`) sono quelle del registro, definite in
`src/registry/schema.sql` e scritte da `src/registry/registry.py`. La domanda
"chi vede questo documento" e' una domanda del REGISTRO, non dell'interfaccia:
metterla sotto `src/ui/` creerebbe una seconda casa per l'RBAC fuori dal
registro, invisibile a qualunque chiamante non-UI (script, futura API, il
percorso di servizio dei file). Il modulo e' inoltre puro Python + psycopg,
quindi testabile senza Chainlit.

Perche' il JOIN e' riscritto e non importato: i due precedenti — `_SELECT` in
`src/graph/tools/sql_tool.py` e `_EXISTS` in `src/retrieval/catalog.py` — sono
ancorati a `estrazione.righe_listino` e rispondono a un'altra domanda ("questo
CODICE esiste per questo ruolo"). Di condiviso hanno le due righe di JOIN su
`documents`/`document_roles`, che qui sono replicate ALLA LETTERA, stessa forma
e stesso ordine dei parametri. Importare una query su `righe_listino` per
ignorarne il soggetto sarebbe accoppiamento senza riuso.

INNER JOIN, mai LEFT: un documento senza riga in `document_roles` e un ruolo
ignoto danno **False**. Un LEFT JOIN restituirebbe la riga del documento con
`dr.*` a NULL, cioe' "visibile a chiunque": esattamente il difetto che questa
famiglia di correzioni sta chiudendo.
"""

from __future__ import annotations

import logging

import psycopg
from psycopg import sql

from src.backend.clients.postgres_client import conninfo_readonly

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA = "public"

# Timeout di connessione: senza, un Postgres irraggiungibile bloccherebbe il
# click dell'utente fino al timeout del sistema operativo. Stesso valore di
# `catalog.CONNECT_TIMEOUT_S`.
CONNECT_TIMEOUT_S = 3

# `schema` e' un Identifier whitelisted dal chiamante, `role` e `doc_stem` sono
# SOLO parametri `%s`: nessuna interpolazione di stringa, nessun LIKE (un `%`
# o un `*` nel doc_stem non e' un jolly, e' un carattere che non matchera' mai).
_VISIBLE = sql.SQL(
    "SELECT 1 FROM {schema}.documents d "
    "JOIN {schema}.document_roles dr ON dr.document_id = d.id AND dr.role = %s "
    "WHERE d.slug = %s LIMIT 1"
)


def _role_str(role: object) -> str:
    """`Role` enum o stringa -> valore normalizzato; qualunque altra cosa -> "".

    Diverge da `catalog._role_str`, che sul ruolo assente SOLLEVA: li' l'unico
    chiamante e' il wiring delle dipendenze, dove un'eccezione esplode una volta
    sola e lontano dall'utente. Qui il chiamante e' un callback di UI su un
    payload che arriva dal client: sollevare significherebbe un'eccezione non
    gestita per ogni click manomesso, con lo stesso esito di sicurezza di un
    `False` (nessun render) ma un comportamento peggiore. Restituire "" —
    che il chiamante traduce in NEGATO — e' fail-closed e silenzioso.
    """
    # `Role` e' una StrEnum: `isinstance(role, str)` e' gia' vero e `str(role)`
    # e' gia' il suo valore. Tutto il resto (None, int, dict) non e' un ruolo.
    if not isinstance(role, str):
        return ""
    return str(role).strip().lower()


def document_visible(
    conn: psycopg.Connection,
    doc_stem: object,
    role: object,
    *,
    schema: str = DEFAULT_SCHEMA,
) -> bool:
    """Connessione INIETTATA (stile `registry.py`): la logica e' testabile su un
    registro sintetico senza toccare quello reale.

    `schema` parametrizzabile per lo stesso motivo di `sql_tool` — isolare i
    test — e MAI da input utente: e' un Identifier.
    """
    if not isinstance(doc_stem, str):
        return False
    stem = doc_stem.strip()
    if not stem:
        return False

    role_value = _role_str(role)
    if not role_value:
        return False

    row = conn.execute(
        _VISIBLE.format(schema=sql.Identifier(schema)), (role_value, stem)
    ).fetchone()
    return row is not None


def doc_visible_to(doc_stem: object, role: object) -> bool:
    """La stessa domanda, con la connessione presa in casa. Non solleva MAI.

    Connessione read-only (`aike_ro`) e nuova a ogni chiamata, non un pool: la
    UI non ne ha uno (`feedback_handler.save_feedback_ui` apre e chiude allo
    stesso modo, una connessione per azione dell'utente) e questa funzione
    risponde a un CLICK, non a un token di generazione. Misurato il 25/08 su
    questa macchina: 7-8 ms a chiamata dopo la prima. Una connessione longeva
    di processo costerebbe la logica di riapertura che `PgCatalog` ha dovuto
    farsi crescere addosso, per guadagnare millisecondi su un gesto umano.

    Ogni eccezione (Postgres giu', permessi mancanti, timeout) degrada a
    **False**: un guasto d'ambiente non e' un permesso di lettura.
    """
    try:
        with psycopg.connect(
            conninfo_readonly(), connect_timeout=CONNECT_TIMEOUT_S
        ) as conn:
            return document_visible(conn, doc_stem, role)
    except Exception as exc:  # noqa: BLE001 — fail-closed, mai propagare a un click
        logger.warning(
            "doc_visible_to: registro non raggiungibile (%s: %s) -> accesso negato",
            type(exc).__name__, exc)
        return False
