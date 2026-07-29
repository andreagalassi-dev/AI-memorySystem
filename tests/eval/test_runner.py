# tests/eval/test_runner.py
from src.eval.truth_set import EvalItem
from src.eval.runner import to_run_output, run_eval

def _item(**kw):
    base = dict(id="i", query="q", lang="it", branch="narrative", role="tecnico",
                doc="d", page=5, expected="foo", kind="positive",
                must_say_nonso=False, source="crop")
    base.update(kw); return EvalItem(**base)

def test_to_run_output_extracts_tuples():
    class E:  # finto Evidence
        def __init__(s,d,p): s.doc=d; s.page=p
    state = {"evidence":[E("d",5)], "visual_pages":[], "citations":[("d",5)],
             "final_level":"ALTA", "final_answer":"foo", "route":"natural","intent":"NARRATIVE"}
    out = to_run_output(state)
    assert out.evidence == [("d",5)] and out.level=="ALTA" and out.answer=="foo"

def test_run_eval_aggregates_per_lang_and_branch():
    items = [_item(id="a", lang="it"), _item(id="b", lang="en", branch="structured",
             doc="d", page=5, expected="foo")]
    def answer_fn(q, role, deps):
        return {"evidence":[type("E",(),{"doc":"d","page":5})()], "visual_pages":[],
                "citations":[("d",5)], "final_level":"ALTA","final_answer":"foo",
                "route":"code","intent":"NARRATIVE"}
    rep = run_eval(items, answer_fn, deps_for_role=lambda r: None, k=10)
    assert rep.per_lang["it"]["n"] == 1
    assert rep.per_branch["narrative"]["recall@k"] == 1.0
    assert rep.per_lang["it"]["correttezza"] == 1.0
