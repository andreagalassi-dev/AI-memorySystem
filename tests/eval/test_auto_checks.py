from src.eval.auto_checks import derive_structural_checks, coverage_row

def test_derive_limits_per_doc():
    def fetch():
        return [("d1",2,"A1",78.83),("d1",3,"A2",10.0),("d1",4,"A3",5.0),
                ("d2",1,"B1",None)]
    items = derive_structural_checks(fetch, per_doc=2)
    d1 = [i for i in items if i.doc=="d1"]
    assert len(d1) == 2
    assert all(i.source=="auto" and i.branch=="structured" for i in items)
    b1 = [i for i in items if i.doc=="d2"][0]
    assert b1.expected is None and b1.query == "codice B1"

def test_coverage_row():
    row = coverage_row("d1", {"d1"}, set(), {"d1"})
    assert row == {"doc":"d1","structured":True,"narrative":False,"visual":True}
