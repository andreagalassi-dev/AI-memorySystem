from src.eval.runner import EvalReport
from scripts.eval_ampio import format_report

def test_format_report_has_lang_and_branch():
    rep = EvalReport(
        per_lang={"it":{"n":2,"recall@k":0.5,"correttezza":1.0,"routing":1.0,"nonso_lazy":1,"nonso_right":0}},
        per_branch={"structured":{"n":2,"recall@k":0.5,"correttezza":1.0,"routing":1.0,"nonso_lazy":1,"nonso_right":0}},
        items=[])
    txt = format_report(rep, coverage=[{"doc":"d","structured":True,"narrative":False,"visual":False}])
    assert "it" in txt and "structured" in txt and "recall@k" in txt
