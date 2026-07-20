"""C2.a — Test ROSSO: routing borderless Mamoli (pagine FAST_TEXT che sono tabelle).

Due test:
1. `test_mamoli_p230_is_table_page`: la pagina 230 del PDF Mamoli reale viene
   riconosciuta come tabella borderless da `is_table_page`. Questo PASSA già (il
   detector funziona).
2. `test_fast_text_table_routed_to_extraction`: il runner massivo instrada una
   pagina FAST_TEXT-ma-tabella all'estrazione testuale producendo >0 righe.
   DEVE FALLIRE prima del fix (oggi il ramo FAST_TEXT fa `continue` cieco).

Usa la pagina REALE p230 del PDF Mamoli (born-digital, text-layer affidabile).
"""

from __future__ import annotations

import fitz
import psycopg
import pytest
from psycopg import sql

from src.backend.clients.postgres_client import conninfo
from src.ingestion.extraction.load import init_schema
from src.ingestion.extraction.table_detect import is_table_page
from src.ingestion.extraction.text_table import TextTableExtractor
from src.ingestion.run_massive_extraction import run_document_massive
from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer
from tests.conftest import needs_pg

MAMOLI_PDF = "corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf"
SCHEMA = "test_c2a_borderless"


# --- Test 1: il detector riconosce la pagina borderless ---

def test_mamoli_p230_is_table_page():
    """is_table_page sulla pagina reale 230 (borderless) deve dare is_table=True."""
    doc = fitz.open(MAMOLI_PDF)
    page = doc.load_page(230 - 1)  # 0-indexed
    verdict = is_table_page(page)
    doc.close()
    assert verdict.is_table, (
        f"is_table_page non rileva p230 come tabella: {verdict.reason}"
    )


# --- Test 2: il runner instrada la pagina FAST_TEXT-tabella all'estrazione ---

pytestmark = needs_pg


@pytest.fixture()
def pgconn():
    ident = sql.Identifier(SCHEMA)
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()
        init_schema(c, schema=SCHEMA)
        yield c
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(ident))
        c.commit()


def test_fast_text_table_routed_to_extraction(pgconn):
    """Una pagina FAST_TEXT che è una tabella borderless deve produrre >0 righe.

    Estrae SOLO p224-231 (max_pages non serve: estraiamo il range sul PDF intero
    filtrando dopo). Per semplicità, estraiamo con max_pages=231 e verifichiamo
    che almeno una pagina nel range 224-231 produca righe.

    Prima del fix: il ramo FAST_TEXT fa `continue` → 0 righe su queste pagine.
    """
    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)

    # VLM che esplode se chiamato (non deve essere usato su born-digital)
    class ExplodingVlm:
        def extract(self, ref):
            raise AssertionError(
                "il VLM non deve essere chiamato su pagina born-digital"
            )

    result = run_document_massive(
        MAMOLI_PDF,
        conn=pgconn,
        extractor=ExplodingVlm(),
        multiplexer=mux,
        model_name="glm-ocr",
        schema=SCHEMA,
        max_pages=231,
        text_extractor_factory=lambda doc: TextTableExtractor(doc),
    )

    # Filtra le pagine 224-231
    pages_224_231 = [p for p in result.pages if 224 <= p.page <= 231]
    righe_224_231 = sum(p.n_valide for p in pages_224_231)

    assert righe_224_231 > 0, (
        f"Nessuna riga estratta da p224-231: le tabelle borderless FAST_TEXT "
        f"non vengono instradate all'estrazione. "
        f"Routes: {[(p.page, p.route, p.n_valide) for p in pages_224_231]}"
    )
