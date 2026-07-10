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

import psycopg
from psycopg import sql

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
