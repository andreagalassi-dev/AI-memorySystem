# src/ingestion/ingest_full.py
"""Epica C.1 — Orchestratore ingestion unificata (composizione pura, fail-closed).

Compone i tre binari (strutturato/narrativo/visivo) per UN documento. Non aggiunge
stato: delega ai sotto-componenti (idempotenti/additivi). Un binario che solleva NON
ferma gli altri: il fallimento finisce in IngestReport.scarti (mai silenzioso)."""
from __future__ import annotations
import time
import os
import fitz

from collections.abc import Callable
from dataclasses import dataclass, field

from src.common.roles import parse_role
from src.ingestion.run_massive_extraction import run_document_massive, get_text_extractor_factory
from src.ingestion import ingest_one
from src.retrieval.pixelrag.ingest import ingest_document as ingest_document_visual
from src.retrieval.pixelrag.index import QdrantVisualIndex
from src.retrieval.qwen3vl import ensure_visual_collection

BranchFn = Callable[[], int]

@dataclass
class IngestReport:
    doc_stem: str
    structured_rows: int = 0
    narrative_chunks: int = 0
    visual_pages: int = 0
    scarti: dict[str, str] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)

def ingest_document_full(doc_stem: str, *, structured: BranchFn,
                         narrative: BranchFn, visual: BranchFn) -> IngestReport:
    report = IngestReport(doc_stem=doc_stem)
    for name, fn, attr in (("structured", structured, "structured_rows"),
                           ("narrative", narrative, "narrative_chunks"),
                           ("visual", visual, "visual_pages")):
        t0 = time.perf_counter()
        try:
            setattr(report, attr, int(fn()))
        except Exception as exc:  # fail-closed per-binario: isola, non propaga
            report.scarti[name] = f"{type(exc).__name__}: {exc}"
        report.timings[name] = round(time.perf_counter() - t0, 3)
    return report

def build_branches(
    pdf_path: str,
    doc_stem: str,
    role: str,
    *,
    conn,
    multiplexer,
    model_name: str,
    extractor,
    embedder,
    qdrant_client,
    schema: str = "estrazione",
    chunks_collection: str | None = None,
    visual_collection: str = "aike_pages_visual",
    max_pages: int = 0
) -> dict[str, BranchFn]:
    from src.registry.registry import register_document
    # Idempotent registration of the document
    register_document(conn, slug=doc_stem, category=None, roles=[parse_role(role)], doc_stem=doc_stem, langs=[])

    def structured() -> int:
        os.environ["AIKE_TABLE_EXTRACTOR"] = "docling"
        res = run_document_massive(
            pdf_path,
            conn=conn,
            extractor=extractor,
            multiplexer=multiplexer,
            model_name=model_name,
            schema=schema,
            max_pages=max_pages,
            text_extractor_factory=get_text_extractor_factory(),
        )
        return res.righe_upsert

    def narrative() -> int:
        res = ingest_one.ingest_document(
            pdf_path,
            doc_stem=doc_stem,
            role=role,
            conn=conn,
            is_update=True,
            collection=chunks_collection,
            extract_limit=max_pages,
        )
        return int(res["n_points"])

    def visual() -> int:
        doc = fitz.open(pdf_path)
        try:
            ensure_visual_collection(qdrant_client, collection=visual_collection)
            index = QdrantVisualIndex(qdrant_client, collection=visual_collection)
            res = ingest_document_visual(
                doc,
                doc_stem=doc_stem,
                roles=[parse_role(role)],
                embedder=embedder,
                index=index,
            )
            return res.visual_pages
        finally:
            doc.close()

    return {"structured": structured, "narrative": narrative, "visual": visual}
