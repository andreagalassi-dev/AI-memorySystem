# src/eval/metrics.py
from __future__ import annotations
from dataclasses import dataclass
from src.eval.truth_set import EvalItem

@dataclass
class RunOutput:
    level: str
    answer: str | None
    citations: list[tuple[str, int]]
    evidence: list[tuple[str, int]]
    visual_pages: list[tuple[str, int]]
    route: str | None
    intent: str | None

def _candidates(item: EvalItem, out: RunOutput) -> list[tuple[str, int]]:
    if item.branch == "structured":
        return out.citations
    if item.branch == "visual":
        return out.visual_pages
    return out.evidence

def recall_at_k(item: EvalItem, out: RunOutput, k: int = 10) -> bool | None:
    if item.doc is None:
        return None
    for doc, page in _candidates(item, out)[:k]:
        if doc == item.doc and (item.page is None or page == item.page):
            return True
    return False

def correctness(item: EvalItem, out: RunOutput) -> bool | None:
    if item.expected is None:
        return None
    return item.expected.lower() in (out.answer or "").lower()

def nonso_status(out: RunOutput) -> str:
    if out.answer is None or (out.level or "").upper() in {"BASSA", "REFUSAL"}:
        return "nonso"
    return "answered"

def routing_match(item: EvalItem, out: RunOutput) -> bool | None:
    if not out.intent:
        return None
    return item.branch.lower() == out.intent.lower()
