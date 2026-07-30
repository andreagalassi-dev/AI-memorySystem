"""Test e2e per ingest_document_full."""

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
from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer

pytestmark = pytest.mark.live

def test_ingest_full_wiring_live():
    uuid8 = uuid.uuid4().hex[:8]
    schema = f"c_spike_{uuid8}"
    chunks_collection = f"chunks_{uuid8}"
    visual_collection = f"visual_{uuid8}"

    doc_path = Path("corpus/price_lists/industeel_steel_surcharges_2026.pdf")
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

        # setup models and extractors
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
                max_pages=1,
            )

            # Primo giro
            r1 = ingest_document_full(doc_stem, **branches)
            assert r1.scarti == {}, f"Ci sono scarti: {r1.scarti}"
            assert r1.structured_rows > 0 or r1.narrative_chunks > 0, "Deve estrarre qualcosa"

            # Secondo giro (idempotenza)
            r2 = ingest_document_full(doc_stem, **branches)
            assert r2.scarti == {}
            assert r2.structured_rows == r1.structured_rows
            assert r2.narrative_chunks == r1.narrative_chunks
            assert r2.visual_pages == r1.visual_pages

    finally:
        # Teardown
        with psycopg.connect(conninfo()) as c:
            c.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
            c.commit()
        if qc.collection_exists(chunks_collection):
            qc.delete_collection(chunks_collection)
        if qc.collection_exists(visual_collection):
            qc.delete_collection(visual_collection)
