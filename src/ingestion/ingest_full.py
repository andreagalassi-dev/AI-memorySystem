# src/ingestion/ingest_full.py
"""Epica C.1 — Orchestratore ingestion unificata (composizione pura, fail-closed).

Compone i tre binari (strutturato/narrativo/visivo) per UN documento. Non aggiunge
stato: delega ai sotto-componenti (idempotenti/additivi). Un binario che solleva NON
ferma gli altri: il fallimento finisce in IngestReport.scarti (mai silenzioso)."""
from __future__ import annotations
import time
from collections.abc import Callable
from dataclasses import dataclass, field

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
