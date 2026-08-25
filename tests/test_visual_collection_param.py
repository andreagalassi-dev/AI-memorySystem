"""Spike C.2 fix — ensure_visual_collection parametrizzato per collezione.

Il finding: il ramo visivo dell'orchestratore falliva con
`UnexpectedResponse 404 - Collection ... doesn't exist` perché
`ensure_visual_collection` era hardcoded su VISUAL_COLLECTION (aike_pages_visual)
e in sandbox (collezione temp) non creava nulla.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from src.backend.clients.postgres_client import conninfo
from src.backend.clients.qdrant_client import get_qdrant_client
from src.ingestion.extraction.extractor import FakeExtractor
from src.ingestion.extraction.load import init_schema
from src.ingestion.ingest_full import build_branches, ingest_document_full
from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder
from src.retrieval.qwen3vl import ensure_visual_collection
from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer


def test_ensure_visual_collection_param_and_idempotence():
    """UNIT (no modelli): la collezione viene creata su un nome arbitrario e
    la seconda chiamata è idempotente (nessuna eccezione)."""
    qc = get_qdrant_client()
    coll = f"test_vis_{uuid.uuid4().hex[:8]}"
    try:
        ensure_visual_collection(qc, collection=coll)
        assert qc.collection_exists(coll) is True

        # Idempotenza: seconda chiamata non solleva, resta True.
        ensure_visual_collection(qc, collection=coll)
        assert qc.collection_exists(coll) is True
    finally:
        if qc.collection_exists(coll):
            qc.delete_collection(coll)


@pytest.fixture
def sandbox_id() -> str:
    """Un solo hash condiviso da schema Postgres e collezioni Qdrant della
    sandbox, cosi' i tre nomi restano accoppiati (`c_vis_<id>`, `chunks_<id>`,
    `visual_<id>`) come nello script di pulizia offline."""
    return uuid.uuid4().hex[:8]


@pytest.fixture
def sandbox_pg_schema(sandbox_id: str):
    """Schema Postgres usa-e-getta, teardown via fixture (non try/finally nel
    corpo del test).

    Perche' una fixture e non un try/finally: pytest esegue sempre le fasi
    setup/call/teardown di un item nell'ordine, e la fase di teardown (qui: il
    codice dopo lo `yield`) gira anche quando la fase `call` viene interrotta
    da un KeyboardInterrupt — verificato sperimentalmente (vedi report della
    task), non solo per sentito dire dalla doc. Un try/finally *dentro* il
    corpo del test copre lo stesso caso SE l'interruzione arriva dentro il suo
    blocco try; il bug reale che questa fixture chiude e' che il vecchio test
    creava lo schema in un primo `try/except` SENZA `finally`, e solo dopo
    apriva un secondo `try/finally` per Qdrant+ingestion: un'interruzione (o
    un'eccezione reale) fra i due blocchi perdeva lo schema senza che nessun
    `finally` lo coprisse. Con una fixture per risorsa, il teardown e'
    accoppiato alla creazione by construction, non per disciplina manuale.

    Limite noto e non risolto da questa fixture: un kill duro del processo
    (SIGKILL, TaskStop del harness, crash) non fa girare NESSUN codice Python,
    ne' un `finally` ne' il teardown di una fixture — per quello serve lo
    strumento offline `scripts/clean_test_sandboxes.py`.
    """
    schema = f"c_vis_{sandbox_id}"
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        c.commit()
        init_schema(c, schema=schema)
    yield schema
    with psycopg.connect(conninfo()) as c:
        c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        c.commit()


@pytest.fixture
def sandbox_qdrant_collections(sandbox_id: str):
    """Collezioni Qdrant usa-e-getta (`chunks_<id>`, `visual_<id>`), stesso
    ragionamento di `sandbox_pg_schema` sul teardown via fixture."""
    qc = get_qdrant_client()
    chunks_collection = f"chunks_{sandbox_id}"
    visual_collection = f"visual_{sandbox_id}"
    if qc.collection_exists(chunks_collection):
        qc.delete_collection(chunks_collection)
    if qc.collection_exists(visual_collection):
        qc.delete_collection(visual_collection)
    yield chunks_collection, visual_collection
    if qc.collection_exists(chunks_collection):
        qc.delete_collection(chunks_collection)
    if qc.collection_exists(visual_collection):
        qc.delete_collection(visual_collection)


@pytest.mark.live
def test_visual_branch_creates_collection_live(sandbox_pg_schema, sandbox_qdrant_collections):
    """LIVE: controprova che il finding è risolto — il ramo visivo produce
    pagine e non finisce negli scarti, in sandbox totale (schema + collezioni
    temp con suffisso uuid, teardown via fixture: vedi sandbox_pg_schema)."""
    schema = sandbox_pg_schema
    chunks_collection, visual_collection = sandbox_qdrant_collections

    doc_path = Path("corpus/gastronomy/kbs_gastrotechnik_4055041.pdf")
    doc_stem = doc_path.stem
    role = "tecnico"

    qc = get_qdrant_client()
    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)
    extractor = FakeExtractor({})
    embedder = Qwen3VLEmbedder()

    with psycopg.connect(conninfo()) as conn:
        branches = build_branches(
            str(doc_path),
            doc_stem,
            role,
            conn=conn,
            multiplexer=mux,
            model_name="glm-ocr",
            extractor=extractor,
            embedder=embedder,
            qdrant_client=qc,
            schema=schema,
            chunks_collection=chunks_collection,
            visual_collection=visual_collection,
            max_pages=0,
        )

        r = ingest_document_full(doc_stem, **branches)

        assert "visual" not in r.scarti, f"Ramo visivo scartato: {r.scarti.get('visual')}"
        assert r.visual_pages > 0, f"Attese pagine visive > 0, ottenuto {r.visual_pages}"
