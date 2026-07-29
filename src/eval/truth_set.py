# src/eval/truth_set.py
from __future__ import annotations
import json
from dataclasses import dataclass

class ValidationError(ValueError):
    pass

_BRANCHES = {"structured", "visual", "narrative"}
_LANGS = {"it", "en", "de", "es"}
_KINDS = {"positive", "negative"}

@dataclass(frozen=True)
class EvalItem:
    id: str
    query: str
    lang: str
    branch: str
    role: str
    doc: str | None
    page: int | None
    expected: str | None
    kind: str
    must_say_nonso: bool
    source: str

def _build(raw: dict) -> EvalItem:
    try:
        item = EvalItem(
            id=str(raw["id"]), query=str(raw["query"]), lang=str(raw["lang"]),
            branch=str(raw["branch"]), role=str(raw.get("role", "tecnico")),
            doc=raw.get("doc"), page=raw.get("page"), expected=raw.get("expected"),
            kind=str(raw.get("kind", "positive")),
            must_say_nonso=bool(raw.get("must_say_nonso", False)),
            source=str(raw.get("source", "crop")),
        )
    except KeyError as e:
        raise ValidationError(f"campo mancante: {e}") from e
    if item.branch not in _BRANCHES:
        raise ValidationError(f"branch invalido: {item.branch!r}")
    if item.lang not in _LANGS:
        raise ValidationError(f"lang invalida: {item.lang!r}")
    if item.kind not in _KINDS:
        raise ValidationError(f"kind invalido: {item.kind!r}")
    return item

def load_truth_set(path: str) -> list[EvalItem]:
    with open(path) as f:
        data = json.load(f)
    return [_build(r) for r in data["items"] if "_section" not in r]
