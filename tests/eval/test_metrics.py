# tests/eval/test_metrics.py
from src.eval.truth_set import EvalItem
from src.eval.metrics import RunOutput, recall_at_k, correctness, nonso_status, routing_match

def _item(**kw):
    base = dict(id="i", query="q", lang="it", branch="narrative", role="tecnico",
                doc="d", page=5, expected=None, kind="positive",
                must_say_nonso=False, source="crop")
    base.update(kw); return EvalItem(**base)

def _out(**kw):
    base = dict(level="ALTA", answer="x", citations=[], evidence=[], visual_pages=[],
                route="natural", intent="NARRATIVE")
    base.update(kw); return RunOutput(**base)

def test_recall_narrative_hit():
    assert recall_at_k(_item(branch="narrative"), _out(evidence=[("d",5),("e",1)]), k=10) is True

def test_recall_miss_and_notapplicable():
    assert recall_at_k(_item(branch="narrative"), _out(evidence=[("e",1)]), k=10) is False
    assert recall_at_k(_item(doc=None, page=None), _out(), k=10) is None

def test_recall_structured_uses_citations():
    assert recall_at_k(_item(branch="structured"), _out(citations=[("d",5)]), k=10) is True

def test_correctness_substring():
    assert correctness(_item(expected="78.83"), _out(answer="prezzo 78.83 EUR")) is True
    assert correctness(_item(expected="99.00"), _out(answer="prezzo 78.83 EUR")) is False
    assert correctness(_item(expected=None), _out()) is None

def test_nonso_and_routing():
    assert nonso_status(_out(level="BASSA")) == "nonso"
    assert nonso_status(_out(level="ALTA")) == "answered"
    assert routing_match(_item(branch="narrative"), _out(intent="NARRATIVE")) is True
    assert routing_match(_item(branch="visual"), _out(intent="NARRATIVE")) is False
