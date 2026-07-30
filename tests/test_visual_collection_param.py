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


@pytest.mark.live
def test_visual_branch_creates_collection_live():
    """LIVE: controprova che il finding è risolto — il ramo visivo produce
    pagine e non finisce negli scarti, in sandbox totale (schema + collezioni
    temp con suffisso uuid, teardown nel finally)."""
    uuid8 = uuid.uuid4().hex[:8]
    schema = f"c_vis_{uuid8}"
    chunks_collection = f"chunks_{uuid8}"
    visual_collection = f"visual_{uuid8}"

    doc_path = Path("corpus/gastronomy/kbs_gastrotechnik_4055041.pdf")
    doc_stem = doc_path.stem
    role = "tecnico"

    qc = get_qdrant_client()

    try:
        with psycopg.connect(conninfo()) as c:
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            c.commit()
            init_schema(c, schema=schema)
    except Exception as exc:
        pytest.fail(f"Failed to setup pg schema: {exc}")

    try:
        if qc.collection_exists(chunks_collection):
            qc.delete_collection(chunks_collection)
        if qc.collection_exists(visual_collection):
            qc.delete_collection(visual_collection)

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

    finally:
        with psycopg.connect(conninfo()) as c:
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            c.commit()
        if qc.collection_exists(chunks_collection):
            qc.delete_collection(chunks_collection)
        if qc.collection_exists(visual_collection):
            qc.delete_collection(visual_collection)
