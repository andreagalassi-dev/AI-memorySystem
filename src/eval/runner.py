# src/eval/runner.py
from __future__ import annotations
from dataclasses import dataclass, field
from src.eval.metrics import (RunOutput, recall_at_k, correctness,
                              nonso_status, routing_match)

def to_run_output(state: dict) -> RunOutput:
    ev = [(e.doc, e.page) for e in state.get("evidence", []) or []]
    vp = [(p.doc, p.page) for p in state.get("visual_pages", []) or []]
    return RunOutput(
        level=state.get("final_level") or state.get("level") or "",
        answer=state.get("final_answer") or state.get("answer"),
        citations=list(state.get("citations", []) or []),
        evidence=ev, visual_pages=vp,
        route=state.get("route"), intent=state.get("intent"),
    )

@dataclass
class EvalReport:
    per_lang: dict = field(default_factory=dict)
    per_branch: dict = field(default_factory=dict)
    items: list = field(default_factory=list)

def _mean(vals: list[bool]) -> float | None:
    xs = [v for v in vals if v is not None]
    return round(sum(1 for v in xs if v) / len(xs), 3) if xs else None

def _aggregate(rows: list[dict]) -> dict:
    return {
        "n": len(rows),
        "recall@k": _mean([r["recall"] for r in rows]),
        "correttezza": _mean([r["correct"] for r in rows]),
        "routing": _mean([r["routing"] for r in rows]),
        "nonso_lazy": sum(1 for r in rows if r["kind"]=="positive" and r["nonso"]=="nonso"),
        "nonso_right": sum(1 for r in rows if r["kind"]=="negative" and r["nonso"]=="nonso"),
    }

def run_eval(items, answer_fn, deps_for_role, k: int = 10) -> EvalReport:
    rows = []
    for it in items:
        state = answer_fn(it.query, it.role, deps_for_role(it.role))
        out = to_run_output(state)
        rows.append({
            "id": it.id, "lang": it.lang, "branch": it.branch, "kind": it.kind,
            "recall": recall_at_k(it, out, k), "correct": correctness(it, out),
            "nonso": nonso_status(out), "routing": routing_match(it, out),
            "level": out.level, "answer": (out.answer or "")[:120],
        })
    rep = EvalReport(items=rows)
    for key in {r["lang"] for r in rows}:
        rep.per_lang[key] = _aggregate([r for r in rows if r["lang"]==key])
    for key in {r["branch"] for r in rows}:
        rep.per_branch[key] = _aggregate([r for r in rows if r["branch"]==key])
    return rep
