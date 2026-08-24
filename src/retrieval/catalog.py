"""Esistenza di un codice a catalogo: SSOT Postgres UNIONE proiezione Qdrant.

Perche' esiste questo modulo. Il guardrail (`retrieval.guardrail.verify_code`) usa un
lookup globale per distinguere due cose molto diverse:

  - `MISS_RETRIEVAL` — il codice esiste a catalogo ma non e' nell'evidenza recuperata:
    la risposta si MOSTRA con "verifica consigliata" (MEDIA);
  - `ABSENT` — il codice non esiste: e' un'allucinazione, la risposta si BLOCCA.

Finche' quel lookup ha interrogato solo `aike_chunks`, la distinzione era sbagliata in
un caso su due: l'architettura del progetto e' **SSOT Postgres -> proiezione Qdrant**
(P4), e la proiezione e' indietro rispetto alla SSOT (documenti con righe di listino e
zero chunk; re-estrazioni che hanno arricchito `righe_listino` senza riproiettare).
Risultato: codici REALI dichiarati allucinazioni e risposte corrette nascoste.

Qui il lookup diventa l'UNIONE delle due fonti — la SSOT `estrazione.righe_listino`
piu' la proiezione — perche' un codice presente in una qualsiasi delle due esiste.

Due dettagli non negoziabili:

1. **Normalizzazione.** Il guardrail passa la forma LETTERALE (`_literal_key`: spazi
   tolti, maiuscolo, `. - /` PRESERVATI). `righe_listino.codice_normalizzato` e' invece
   la forma `codes.canonical` (solo `[0-9A-Z]`) — verificato sulle 169567 righe reali:
   `canonical(codice_originale) == codice_normalizzato` sempre. Quindi la SSOT si
   interroga con `canonical(key)`, Qdrant con la chiave letterale. Cercare la forma
   letterale in `codice_normalizzato` non troverebbe nulla (silenziosamente).

2. **Degrado, non rumore.** Se Postgres e' irraggiungibile il ramo SSOT ritorna False e
   si resta al comportamento storico (solo Qdrant): mai un'eccezione da qui. Gli errori
   di **Qdrant** invece continuano a propagare, perche' `verify_code` li cattura e
   restituisce `MISS_RETRIEVAL` — la scelta prudente, che non va soffocata.

La connessione e' UNA sola per conninfo (singleton di processo, `autocommit`, riaperta
se cade), non una per token: un lookup costa ~1.5 ms e i candidati per risposta sono
pochi, ma il grafo ne fa molti in sequenza.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

import psycopg
from psycopg import sql

from src.ingestion.codes import canonical

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA = "estrazione"
DEFAULT_COLLECTION = "aike_chunks_v2"
# Postgres giu' non deve rallentare il guardrail: connect breve, e dopo un fallimento
# il ramo SSOT resta spento per un po' invece di ritentare (e stallare) a ogni token.
CONNECT_TIMEOUT_S = 3
BREAKER_S = 30.0

# Solo esistenza: nessuna colonna, LIMIT 1. Il codice viaggia come parametro `%s`
# (ed e' gia' ridotto a [0-9A-Z] da `canonical`: doppia barriera anti-injection).
_EXISTS = sql.SQL(
    "SELECT 1 FROM {schema}.righe_listino WHERE codice_normalizzato = %s LIMIT 1")


class PgCatalog:
    """Presenza di un codice nella SSOT `estrazione.righe_listino`.

    Thread-safe (una connessione condivisa sotto lock) e non solleva mai: un errore
    Postgres diventa `False` + warning, cioe' il comportamento pre-esistente.
    """

    def __init__(self, conninfo: str, schema: str = DEFAULT_SCHEMA) -> None:
        self.conninfo = conninfo
        self.schema = schema
        self._sql = _EXISTS.format(schema=sql.Identifier(schema))
        self._conn: psycopg.Connection | None = None
        self._lock = threading.Lock()
        self._down_until = 0.0

    # -- connessione ------------------------------------------------------
    def _connection(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            # autocommit: una connessione longeva non deve restare "idle in
            # transaction" trattenendo uno snapshot del DB.
            self._conn = psycopg.connect(
                self.conninfo, autocommit=True, connect_timeout=CONNECT_TIMEOUT_S)
        return self._conn

    def close(self) -> None:
        with self._lock:
            self._drop()

    def _drop(self) -> None:
        try:
            if self._conn is not None and not self._conn.closed:
                self._conn.close()
        except Exception:  # noqa: BLE001 — chiusura best-effort
            pass
        self._conn = None

    # -- lookup -----------------------------------------------------------
    def contains(self, key: str) -> bool:
        code = canonical(key)
        if not code:
            return False
        with self._lock:
            if time.monotonic() < self._down_until:
                return False          # SSOT dichiarata giu': non ristallare per ogni token
            live = self._conn is not None and not self._conn.closed
            try:
                return self._select(code)
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                self._drop()
                if live:
                    # la connessione c'era ed e' caduta (restart del compose, idle
                    # timeout): un solo tentativo di riaprirla, qui e ora.
                    logger.warning("PgCatalog: connessione persa (%s), riapro", exc)
                    try:
                        return self._select(code)
                    except Exception as exc2:  # noqa: BLE001
                        self._drop()
                        exc = exc2
                return self._degrade(exc)
            except Exception as exc:  # noqa: BLE001 — mai propagare: degrado, non crash
                return self._degrade(exc)

    def _degrade(self, exc: BaseException) -> bool:
        self._down_until = time.monotonic() + BREAKER_S
        logger.warning(
            "PgCatalog: SSOT non raggiungibile (%s: %s) -> per %.0fs resta il solo "
            "controllo Qdrant", type(exc).__name__, exc, BREAKER_S)
        return False

    def _select(self, code: str) -> bool:
        with self._connection().cursor() as cur:
            cur.execute(self._sql, (code,))
            return cur.fetchone() is not None


_CATALOGS: dict[tuple[str, str], PgCatalog] = {}
_CATALOGS_LOCK = threading.Lock()


def get_pg_catalog(conninfo: str, schema: str = DEFAULT_SCHEMA) -> PgCatalog:
    """Singleton di processo per (conninfo, schema): una connessione, non una per Deps.

    `answer()` senza deps ricostruisce le dipendenze a ogni chiamata (gate, smoke): un
    `PgCatalog` per Deps aprirebbe centinaia di connessioni.
    """
    key = (conninfo, schema)
    with _CATALOGS_LOCK:
        cat = _CATALOGS.get(key)
        if cat is None:
            cat = PgCatalog(conninfo, schema)
            _CATALOGS[key] = cat
        return cat


def make_catalog_lookup(
    qdrant_client,
    pg_catalog: PgCatalog | None = None,
    collection: str = DEFAULT_COLLECTION,
) -> Callable[[str], bool]:
    """Lookup di catalogo: SSOT prima (e' la piu' completa, ~3 ms come Qdrant: chi
    risponde per primo evita la seconda interrogazione), proiezione Qdrant poi.

    `pg_catalog=None` = nessuna SSOT disponibile -> comportamento storico (solo Qdrant).
    """
    from qdrant_client import models

    def _in_projection(key: str) -> bool:
        r = qdrant_client.count(collection, exact=True, count_filter=models.Filter(
            must=[models.FieldCondition(key="code", match=models.MatchValue(value=key))]))
        return r.count > 0

    def catalog_lookup(key: str) -> bool:
        if pg_catalog is not None and pg_catalog.contains(key):
            return True
        # errori Qdrant NON catturati: `verify_code` li traduce in MISS_RETRIEVAL
        return _in_projection(key)

    return catalog_lookup
