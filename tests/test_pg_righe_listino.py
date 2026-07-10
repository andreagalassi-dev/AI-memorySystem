"""Fase 2.1 — Reader read-only streaming di `righe_listino` (`PgRigheListino`) — RED.

Il reader alimenta `merge()` con i `RigaListino` (il "prodotto" dell'Ipotesi B).
Vincolo anti-OOM: su un listino da migliaia di righe NON si carica l'intera tabella
in RAM. Il Port funge da **generatore** (`Iterable[RigaListino]`) e l'adapter reale
usa un **server-side cursor** Postgres (fetch a chunk), mai un `fetchall()` unico.

Integrazione (`needs_pg`), schema usa-e-getta `test_merge`: `righe_listino` creata
dal DDL di estrazione (`extraction.load.init_schema`) e popolata con `upsert_righe`.
Identificatori via `psycopg.sql` (mai f-string sullo schema).
"""

from __future__ import annotations

import types
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql

from src.backend.clients.postgres_client import conninfo
from src.ingestion.extraction.load import init_schema as init_extraction_schema
from src.ingestion.extraction.load import upsert_righe
from src.ingestion.extraction.schema import RigaGrezza, RigaListino
from src.merge.store import PgRigheListino
from tests.conftest import needs_pg

pytestmark = needs_pg

SCHEMA = "test_merge"


@pytest.fixture()
def pgconn():
    """Schema usa-e-getta con `righe_listino` (DDL di estrazione)."""
    ident = sql.Identifier(SCHEMA)
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()
        init_extraction_schema(c, schema=SCHEMA)
        yield c
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()


def _riga(codice: str, doc_stem: str, page: int, prezzo: str = "10.00") -> RigaListino:
    return RigaListino.from_grezza(
        RigaGrezza(codice_originale=codice, prezzo=prezzo), doc_stem=doc_stem, page=page)


class TestPgRigheListino:
    def test_iter_righe_e_un_generatore(self, pgconn):
        # contratto streaming: iter_righe() e' pigro (generatore), non materializza una lista
        reader = PgRigheListino(pgconn, schema=SCHEMA)
        assert isinstance(reader.iter_righe(), types.GeneratorType)

    def test_round_trip_righe(self, pgconn):
        upsert_righe(pgconn, [_riga("0US-123.A", "mamoli_2021", 3, "12.50")], schema=SCHEMA)
        reader = PgRigheListino(pgconn, schema=SCHEMA)

        righe = list(reader.iter_righe())

        assert len(righe) == 1
        r = righe[0]
        assert (r.doc_stem, r.page, r.codice_normalizzato, r.codice_originale, r.prezzo) == (
            "mamoli_2021", 3, "0US123A", "0US-123.A", Decimal("12.50"))

    def test_stream_oltre_il_chunk_size(self, pgconn):
        # 5 righe con chunk_size=2 -> il cursore server-side fa piu' fetch, senza perdere righe
        upsert_righe(pgconn, [_riga(f"A{p}", "mamoli_2021", p) for p in (1, 2, 3, 4, 5)],
                     schema=SCHEMA)
        reader = PgRigheListino(pgconn, schema=SCHEMA, chunk_size=2)

        pages = [r.page for r in reader.iter_righe()]

        assert pages == [1, 2, 3, 4, 5]  # tutte le righe, ordine deterministico
