"""Fase 1.2 — Persistenza su PostgreSQL (SSOT): righe valide + scarti.

Ultimo stadio del binario di estrazione: le righe che hanno superato ENTRAMBE le
barriere Fail-Closed (forma Pydantic + grounding verbatim) vengono upsert-ate in
`estrazione.righe_listino`; le righe scartate finiscono in
`estrazione.scarti_estrazione` per la revisione manuale (dashboard Fase 2.2). Mai
un dato non validato entra in silenzio nelle tabelle-dati.

Convenzioni allineate a `src/registry` (P4.1): connessione **iniettata**, ogni
funzione di scrittura e' un'unita' transazionale (commit interno), DDL idempotente
(`IF NOT EXISTS`). Lo schema e' **parametrizzabile** (`schema=`) per isolare i test
su uno schema usa-e-getta senza toccare la produzione — stesso spirito del parametro
`collection` in `ingest_one.py`. Gli identificatori sono composti con `psycopg.sql`
(mai f-string sul nome schema).

Design: docs/superpowers/specs/2026-07-09-fase1.2-estrazione-massiva-design.md.
"""

from __future__ import annotations

import logging

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from src.ingestion.extraction.schema import RigaListino, Scarto

logger = logging.getLogger(__name__)

DEFAULT_SCHEMA = "estrazione"

_DDL_SCHEMA = sql.SQL("CREATE SCHEMA IF NOT EXISTS {schema}")

_DDL_RIGHE = sql.SQL(
    """
    CREATE TABLE IF NOT EXISTS {schema}.righe_listino (
        doc_stem            text        NOT NULL,
        page                int         NOT NULL,
        codice_normalizzato text        NOT NULL,
        codice_originale    text        NOT NULL,
        descrizione         text,
        prezzo              numeric,
        updated_at          timestamptz NOT NULL DEFAULT now()
    )
    """
)

_DDL_SCARTI = sql.SQL(
    """
    CREATE TABLE IF NOT EXISTS {schema}.scarti_estrazione (
        id                          bigserial   PRIMARY KEY,
        doc_stem                    text        NOT NULL,
        page                        int         NOT NULL,
        codice_originale            text,
        codice_normalizzato_tentato text,
        payload_raw                 jsonb,
        motivo                      text        NOT NULL,
        created_at                  timestamptz NOT NULL DEFAULT now()
    )
    """
)

_UPSERT_RIGHE = sql.SQL(
    """
    INSERT INTO {schema}.righe_listino
        (doc_stem, page, codice_normalizzato, codice_originale, descrizione, prezzo, updated_at)
    VALUES (%s, %s, %s, %s, %s, %s, now())
    ON CONFLICT (doc_stem, page, codice_normalizzato, (COALESCE(prezzo, -1))) DO UPDATE SET
        codice_originale = EXCLUDED.codice_originale,
        descrizione      = EXCLUDED.descrizione,
        updated_at       = now()
    """
)

_INSERT_SCARTI = sql.SQL(
    """
    INSERT INTO {schema}.scarti_estrazione
        (doc_stem, page, codice_originale, codice_normalizzato_tentato, payload_raw, motivo)
    VALUES (%s, %s, %s, %s, %s, %s)
    """
)


def init_schema(conn: psycopg.Connection, *, schema: str = DEFAULT_SCHEMA) -> None:
    """Crea schema e tabelle se assenti (idempotente). Commit interno."""
    ident = sql.Identifier(schema)
    conn.execute(_DDL_SCHEMA.format(schema=ident))
    conn.execute(_DDL_RIGHE.format(schema=ident))
    conn.execute(_DDL_SCARTI.format(schema=ident))
    
    conn.execute(sql.SQL("ALTER TABLE {schema}.righe_listino DROP CONSTRAINT IF EXISTS righe_listino_pkey;").format(schema=ident))
    conn.execute(sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS righe_listino_uq ON {schema}.righe_listino (doc_stem, page, codice_normalizzato, (COALESCE(prezzo, -1)));").format(schema=ident))
    conn.commit()


def upsert_righe(conn: psycopg.Connection, righe: list[RigaListino], *,
                 schema: str = DEFAULT_SCHEMA) -> int:
    """Upsert idempotente delle righe valide (chiave doc_stem+page+codice_normalizzato).

    Re-ingest della stessa pagina aggiorna in loco: nessun duplicato. Ritorna il
    numero di righe scritte.
    """
    if not righe:
        return 0
    q = _UPSERT_RIGHE.format(schema=sql.Identifier(schema))
    params = [(r.doc_stem, r.page, r.codice_normalizzato, r.codice_originale,
               r.descrizione, r.prezzo) for r in righe]
    with conn.cursor() as cur:
        cur.executemany(q, params)
    conn.commit()
    return len(righe)


def insert_scarti(conn: psycopg.Connection, scarti: list[Scarto], *,
                  schema: str = DEFAULT_SCHEMA) -> int:
    """Registra gli scarti (append-only, con `payload_raw` in jsonb). Commit interno."""
    if not scarti:
        return 0
    q = _INSERT_SCARTI.format(schema=sql.Identifier(schema))
    params = [(s.doc_stem, s.page, s.codice_originale, s.codice_normalizzato_tentato,
               Jsonb(s.payload_raw) if s.payload_raw is not None else None,
               s.motivo.value) for s in scarti]
    with conn.cursor() as cur:
        cur.executemany(q, params)
    conn.commit()
    return len(scarti)


def warn_intra_page_collisions(valide: list[RigaListino]) -> int:
    """Osservabilita': logga (WARNING) i codici che compaiono piu' volte sulla STESSA
    pagina con prezzo DIVERSO. Dall'indice unico su (doc,page,code,COALESCE(prezzo,-1))
    queste righe ora vengono PRESERVATE (non piu' sovrascritte in silenzio), ma vanno
    rese visibili: mai una collisione muta. Puro rispetto al DB; ritorna il numero di
    collisioni rilevate. Va chiamato PRIMA dell'upsert, su entrambi i binari di persist.
    """
    seen: dict[tuple, set] = {}
    for r in valide:
        seen.setdefault((r.doc_stem, r.page, r.codice_normalizzato), set()).add(r.prezzo)
    n = 0
    for (doc_stem, page, code), prices in seen.items():
        if len(prices) > 1:
            n += 1
            logger.warning(
                "Collisione intra-pagina preservata: doc=%s pagina=%d codice=%s prezzi=%s",
                doc_stem, page, code, sorted(str(p) for p in prices),
            )
    return n


def persist(conn: psycopg.Connection, valide: list[RigaListino], scarti: list[Scarto],
            *, schema: str = DEFAULT_SCHEMA) -> dict:
    """Persiste l'esito di una pagina: upsert delle valide + insert degli scarti.

    Ritorna un riepilogo {righe_upsert, scarti}. Le due scritture sono idempotenti
    lato righe (upsert) e append lato scarti (audit).
    """
    n_valide = upsert_righe(conn, valide, schema=schema)
    n_scarti = insert_scarti(conn, scarti, schema=schema)
    logger.info("load: schema=%s righe_upsert=%d scarti=%d", schema, n_valide, n_scarti)
    return {"righe_upsert": n_valide, "scarti": n_scarti}
