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

Tre dettagli non negoziabili:

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

3. **RBAC.** Il lookup e' un ORACOLO DI ESISTENZA, e fino al 25/08 rispondeva su
   documenti che il ruolo della sessione non puo' vedere: il ramo SSOT non toccava
   `public.document_roles` e il ramo proiezione filtrava sul solo `code`, ignorando
   `ruoli_consentiti` (chiave presente nel payload di `aike_chunks_v2`). Misure sul
   dato vivo: dei **73561** codici distinti di `righe_listino` solo **1072** sono
   visibili al `commerciale` — 72489 invisibili, il **98.5%**. Campioni verificati
   nella proiezione, `count` senza ruolo vs con `commerciale`: `877615`
   (rotax_912_914_IPC) 2 -> 0, `45440050` (valvorobica_flange_raccordi_listino) 1 -> 0,
   `550106A` (era_sensori_catalogo) 155 -> 0. Conseguenza: per un commerciale un
   codice tecnico-only risultava "a catalogo": il verdetto scivolava da `ABSENT`
   (BLOCK) a `MISS_RETRIEVAL`, cioe' risposta MOSTRATA a MEDIA. Il guardrail
   diventava piu' permissivo proprio dove doveva essere piu' severo. E' la stessa
   famiglia della falla del ramo strutturato (`graph/tools/sql_tool.py`, corretta
   lo stesso giorno): li' passava il prezzo, qui l'esistenza. Ora **entrambi** i rami
   filtrano per ruolo, e il `role` e' un parametro OBBLIGATORIO SENZA DEFAULT in
   `make_catalog_lookup`/`PgCatalog.contains`: un default silenzioso e' esattamente
   il modo in cui questa falla e' nata.

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
# Importata, non duplicata: un secondo letterale rendeva falso il "rollback =
# una riga" dichiarato in 541ccb2 (erano due) e un rollback parziale avrebbe
# separato il lookup del guardrail dal retriever — due componenti che DEVONO
# vedere lo stesso indice.
from src.ingestion.embedding import COLLECTION as DEFAULT_COLLECTION  # noqa: E402
# Postgres giu' non deve rallentare il guardrail: connect breve, e dopo un fallimento
# il ramo SSOT resta spento per un po' invece di ritentare (e stallare) a ogni token.
CONNECT_TIMEOUT_S = 3
BREAKER_S = 30.0

# Solo esistenza: nessuna colonna, LIMIT 1. Il codice viaggia come parametro `%s`
# (ed e' gia' ridotto a [0-9A-Z] da `canonical`: doppia barriera anti-injection).
# Il JOIN e' lo STESSO di `graph/tools/sql_tool.py::_SELECT`, deliberatamente: due
# porte sulla stessa tabella devono avere la stessa serratura. INNER JOIN, mai LEFT:
# un documento senza riga in `document_roles`, o un ruolo che non esiste, vedono
# ZERO invece di vedere tutto (fail-closed per costruzione).
# Parametri, in ordine: (role, codice_normalizzato).
_EXISTS = sql.SQL(
    "SELECT 1 FROM {schema}.righe_listino r "
    "JOIN public.documents d ON d.slug = r.doc_stem "
    "JOIN public.document_roles dr ON dr.document_id = d.id AND dr.role = %s "
    "WHERE r.codice_normalizzato = %s LIMIT 1")


def _role_str(role) -> str:
    """Normalizza il ruolo in UN SOLO punto: `Role` enum o stringa -> stringa.

    `Role` e' una `StrEnum`, quindi `str(role)` e' gia' il valore. Per le stringhe
    si applica la stessa normalizzazione di `common.roles.parse_role` (strip +
    lower), ma NON la sua validazione a insieme chiuso: un ruolo ignoto non deve
    sollevare, deve semplicemente non trovare nulla — l'INNER JOIN e il filtro
    Qdrant lo rendono fail-closed per costruzione, come in `sql_tool`. Sollevare
    qui sarebbe anche PEGGIO che inutile: `verify_code` cattura le eccezioni del
    lookup e le traduce in `MISS_RETRIEVAL`, cioe' in risposta MOSTRATA.

    Il ruolo ASSENTE e' invece un caso diverso: vedi `PgCatalog.contains`.
    """
    if role is None:
        raise ValueError(
            "role mancante: il lookup di catalogo non interroga nulla senza un "
            "ruolo (fail-closed, SPEC §F.3)")
    value = str(role).strip().lower()
    if not value:
        raise ValueError(
            "role vuoto: il lookup di catalogo non interroga nulla senza un "
            "ruolo (fail-closed, SPEC §F.3)")
    return value


class PgCatalog:
    """Presenza di un codice nella SSOT `estrazione.righe_listino`, PER RUOLO.

    Thread-safe (una connessione condivisa sotto lock). Non solleva per i guasti
    d'AMBIENTE: un errore Postgres diventa `False` + warning, cioe' il comportamento
    pre-esistente. Solleva invece per il ruolo mancante, che e' un errore di
    PROGRAMMAZIONE — vedi `contains`.
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
    def contains(self, key: str, role) -> bool:
        """`key` letterale, `role` OBBLIGATORIO (`Role` o stringa).

        Il ruolo mancante SOLLEVA, e questo non rompe il contratto "contains non
        solleva mai": quel contratto copre i guasti d'AMBIENTE (Postgres giu'),
        dove degradare a `False` conserva il comportamento storico. Un ruolo
        assente e' invece un bug del chiamante, e degradare a `False` sarebbe
        anche INSICURO: il chiamante proseguirebbe sul ramo Qdrant e il codice
        tornerebbe "a catalogo" per la porta accanto. In produzione l'eccezione
        non arriva mai per token, perche' `make_catalog_lookup` valida il ruolo
        una volta sola al wiring. Stessa scelta di `PgSqlExecutor.run`.
        """
        role_value = _role_str(role)          # prima di tutto: nessuna query senza ruolo
        code = canonical(key)
        if not code:
            return False
        with self._lock:
            if time.monotonic() < self._down_until:
                return False          # SSOT dichiarata giu': non ristallare per ogni token
            live = self._conn is not None and not self._conn.closed
            try:
                return self._select(code, role_value)
            except (psycopg.OperationalError, psycopg.InterfaceError) as exc:
                self._drop()
                if live:
                    # la connessione c'era ed e' caduta (restart del compose, idle
                    # timeout): un solo tentativo di riaprirla, qui e ora.
                    logger.warning("PgCatalog: connessione persa (%s), riapro", exc)
                    try:
                        return self._select(code, role_value)
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

    def _select(self, code: str, role: str) -> bool:
        with self._connection().cursor() as cur:
            cur.execute(self._sql, (role, code))
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
    pg_catalog: PgCatalog | None,
    role,
    collection: str = DEFAULT_COLLECTION,
) -> Callable[[str], bool]:
    """Lookup di catalogo PER RUOLO: SSOT prima (e' la piu' completa, ~3 ms come
    Qdrant: chi risponde per primo evita la seconda interrogazione), proiezione
    Qdrant poi.

    `role` e' OBBLIGATORIO e SENZA DEFAULT — anche `pg_catalog` perde il suo, perche'
    un parametro obbligatorio non puo' seguirne uno con default. Costringere ogni
    call-site a decidere e' il punto: il default silenzioso e' come questa falla e'
    nata. Il ruolo si normalizza QUI, una volta sola, e si valida QUI, al wiring:
    cosi' un ruolo mancante esplode dove si costruiscono le dipendenze e non diventa
    un'eccezione per token che `verify_code` tradurrebbe in `MISS_RETRIEVAL`
    (= risposta mostrata), cioe' nel piu' permissivo dei verdetti.

    `pg_catalog=None` = nessuna SSOT disponibile -> resta il solo Qdrant, comunque
    filtrato per ruolo.
    """
    from qdrant_client import models

    role_value = _role_str(role)

    def _in_projection(key: str) -> bool:
        # `ruoli_consentiti` e' una LISTA nel payload: `MatchValue` su una lista
        # matcha per appartenenza. In `must`, quindi in AND col codice: senza questa
        # condizione la proiezione rispondeva "esiste" su documenti di altri ruoli.
        r = qdrant_client.count(collection, exact=True, count_filter=models.Filter(
            must=[
                models.FieldCondition(key="code", match=models.MatchValue(value=key)),
                models.FieldCondition(key="ruoli_consentiti",
                                      match=models.MatchValue(value=role_value)),
            ]))
        return r.count > 0

    def catalog_lookup(key: str) -> bool:
        if pg_catalog is not None and pg_catalog.contains(key, role_value):
            return True
        # errori Qdrant NON catturati: `verify_code` li traduce in MISS_RETRIEVAL
        return _in_projection(key)

    return catalog_lookup
