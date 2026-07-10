"""Fase 2.1 — Persistenza degli scarti di merge su PostgreSQL (SSOT).

Gli scarti di *merge* (righe-prezzo che non si sono potute cucire a una pagina
visiva, §2.2) sono DISTINTI dagli scarti di *estrazione* 1.2 (`scarti_estrazione`):
diverso motivo, diverso ciclo di vita (revisione umana + "Forza Associazione" in
dashboard). Vivono in una tabella dedicata `estrazione.scarti_merge`.

Convenzioni allineate a `src/ingestion/extraction/load.py` (P4.1): connessione
**iniettata**, ogni funzione di scrittura e' un'unita' transazionale (commit
interno), DDL idempotente (`IF NOT EXISTS`). Lo schema e' **parametrizzabile**
(`schema=`) per isolare i test su schema usa-e-getta senza toccare la produzione.
Gli identificatori sono composti con `psycopg.sql` (mai f-string sul nome schema).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass

import psycopg
from psycopg import sql

from src.ingestion.extraction.schema import RigaListino
from src.merge.relational import ScartoMerge

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA = "estrazione"

_DDL_SCHEMA = sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}")

_DDL_SCARTI_MERGE = sql.SQL(
    """
    CREATE TABLE IF NOT EXISTS {schema}.scarti_merge (
        id                          bigserial   PRIMARY KEY,
        doc_stem                    text        NOT NULL,
        page                        int         NOT NULL,
        codice_originale            text,
        codice_normalizzato_tentato text,
        motivo                      text        NOT NULL,
        created_at                  timestamptz NOT NULL DEFAULT now()
    )
    """
)

_INSERT_SCARTI_MERGE = sql.SQL(
    """
    INSERT INTO {schema}.scarti_merge
        (doc_stem, page, codice_originale, codice_normalizzato_tentato, motivo)
    VALUES (%s, %s, %s, %s, %s)
    """
)


_SELECT_RIGHE = sql.SQL(
    "SELECT codice_originale, descrizione, prezzo, codice_normalizzato, doc_stem, page "
    "FROM {schema}.righe_listino"
)
_WHERE_DOC = sql.SQL(" WHERE doc_stem = %s")
_ORDER_RIGHE = sql.SQL(" ORDER BY doc_stem, page, codice_normalizzato")

_SELECT_DOC_STEMS = sql.SQL(
    "SELECT DISTINCT doc_stem FROM {schema}.righe_listino ORDER BY doc_stem"
)
_DELETE_SCARTI_DOC = sql.SQL("DELETE FROM {schema}.scarti_merge WHERE doc_stem = %s")


def get_distinct_doc_stems(conn: psycopg.Connection, *,
                           schema: str = DEFAULT_SCHEMA) -> list[str]:
    """I `doc_stem` presenti in `righe_listino` (ordinati). Serve all'orchestratore
    per ciclare un documento alla volta (anti-OOM)."""
    q = _SELECT_DOC_STEMS.format(schema=sql.Identifier(schema))
    return [r[0] for r in conn.execute(q).fetchall()]


def replace_scarti_for_doc(conn: psycopg.Connection, doc_stem: str,
                           scarti: list[ScartoMerge], *,
                           schema: str = DEFAULT_SCHEMA) -> int:
    """Idempotenza per documento: delete mirato (`WHERE doc_stem`) + insert dei nuovi
    scarti, ATOMICI in una transazione (commit per doc). Un re-run non duplica; un doc
    che non ha piu' orfani vede i suoi scarti obsoleti purgati (insert vuoto = solo
    delete). Ritorna il numero di scarti scritti."""
    q_del = _DELETE_SCARTI_DOC.format(schema=sql.Identifier(schema))
    q_ins = _INSERT_SCARTI_MERGE.format(schema=sql.Identifier(schema))
    params = [(s.doc_stem, s.page, s.codice_originale, s.codice_normalizzato_tentato,
               s.motivo.value) for s in scarti]
    with conn.transaction():
        with conn.cursor() as cur:
            cur.execute(q_del, (doc_stem,))
            if params:
                cur.executemany(q_ins, params)
    logger.info("merge.store: replace schema=%s doc=%s scarti=%d", schema, doc_stem,
                len(scarti))
    return len(scarti)


@dataclass
class PgRigheListino:
    """Reader read-only STREAMING di `righe_listino` -> `RigaListino` (input di `merge`).

    Anti-OOM: un listino da migliaia di righe non entra in RAM in una lista. `iter_righe`
    e' un generatore che scorre un **server-side cursor** Postgres (`conn.cursor(name=...)`,
    fetch a `chunk_size`), mai un `fetchall()` unico. Il cursore nominato esige una
    transazione: `with conn.transaction()` la garantisce anche se `conn` e' in autocommit
    (e usa un savepoint se una transazione e' gia' aperta). Connessione INIETTATA e schema
    parametrizzabile (test usa-e-getta), come `PgSqlExecutor`. Solo SELECT: read-only."""

    conn: psycopg.Connection
    schema: str = DEFAULT_SCHEMA
    chunk_size: int = 1000

    def iter_righe(self, doc_stem: str | None = None) -> Iterator[RigaListino]:
        q = _SELECT_RIGHE.format(schema=sql.Identifier(self.schema))
        params: tuple = ()
        if doc_stem is not None:                 # filtro opzionale: un documento alla volta
            q = q + _WHERE_DOC
            params = (doc_stem,)
        q = q + _ORDER_RIGHE
        with self.conn.transaction():
            with self.conn.cursor(name="righe_listino_stream") as cur:
                cur.itersize = self.chunk_size
                cur.execute(q, params)
                for cod_orig, descr, prezzo, cod_norm, doc_stem, page in cur:
                    yield RigaListino(
                        codice_originale=cod_orig,
                        descrizione=descr,
                        prezzo=prezzo,
                        codice_normalizzato=cod_norm,
                        doc_stem=doc_stem,
                        page=page,
                    )


def init_schema(conn: psycopg.Connection, *, schema: str = DEFAULT_SCHEMA) -> None:
    """Crea schema e tabella `scarti_merge` se assenti (idempotente). Commit interno."""
    ident = sql.Identifier(schema)
    conn.execute(_DDL_SCHEMA.format(schema=ident))
    conn.execute(_DDL_SCARTI_MERGE.format(schema=ident))
    conn.commit()


def persist_scarti(conn: psycopg.Connection, scarti: list[ScartoMerge], *,
                   schema: str = DEFAULT_SCHEMA) -> int:
    """Registra gli scarti di merge (append-only, audit). Commit interno.

    `created_at` e' valorizzato dal default di colonna. Ritorna il numero di righe
    inserite; lista vuota -> no-op (0)."""
    if not scarti:
        return 0
    q = _INSERT_SCARTI_MERGE.format(schema=sql.Identifier(schema))
    params = [(s.doc_stem, s.page, s.codice_originale, s.codice_normalizzato_tentato,
               s.motivo.value) for s in scarti]
    with conn.cursor() as cur:
        cur.executemany(q, params)
    conn.commit()
    logger.info("merge.store: schema=%s scarti_merge=%d", schema, len(scarti))
    return len(scarti)
