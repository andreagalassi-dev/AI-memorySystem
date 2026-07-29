from __future__ import annotations
from collections import defaultdict
from src.eval.truth_set import EvalItem

def derive_structural_checks(fetch_codes, per_doc: int = 3) -> list[EvalItem]:
    seen: dict[str, int] = defaultdict(int)
    out: list[EvalItem] = []
    for doc, page, code, prezzo in fetch_codes():
        if seen[doc] >= per_doc:
            continue
        seen[doc] += 1
        out.append(EvalItem(
            id=f"AUTO-{doc}-{code}", query=f"codice {code}", lang="it",
            branch="structured", role="tecnico", doc=doc, page=page,
            expected=(str(prezzo) if prezzo is not None else None),
            kind="positive", must_say_nonso=False, source="auto",
        ))
    return out

def coverage_row(doc_stem, structured_docs, narrative_docs, visual_docs) -> dict:
    return {
        "doc": doc_stem,
        "structured": doc_stem in structured_docs,
        "narrative": doc_stem in narrative_docs,
        "visual": doc_stem in visual_docs,
    }
