# tests/eval/test_truth_set.py
import json, pytest
from src.eval.truth_set import EvalItem, load_truth_set, ValidationError

def _write(tmp_path, items):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"items": items}))
    return str(p)

def test_load_valid_item(tmp_path):
    path = _write(tmp_path, [
        {"id":"S1","query":"codice X","lang":"it","branch":"structured",
         "role":"tecnico","doc":"d","page":2,"expected":"78.83",
         "kind":"positive","must_say_nonso":False,"source":"crop"},
        {"_section":"commento da ignorare"},
    ])
    items = load_truth_set(path)
    assert len(items) == 1
    assert isinstance(items[0], EvalItem)
    assert items[0].branch == "structured" and items[0].page == 2

def test_bad_branch_rejected(tmp_path):
    path = _write(tmp_path, [
        {"id":"S1","query":"q","lang":"it","branch":"WRONG","role":"tecnico",
         "doc":None,"page":None,"expected":None,"kind":"negative",
         "must_say_nonso":True,"source":"crop"}])
    with pytest.raises(ValidationError):
        load_truth_set(path)
