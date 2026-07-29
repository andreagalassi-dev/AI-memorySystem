# tests/eval/test_plumbing.py
from src.eval.truth_set import EvalItem
from src.eval.plumbing import build_plumbing_items, build_coverage_row

def test_build_plumbing_items():
    rows = [
        ("docA", 10, "COD123", "Pezzo di ferro", 100.50),
        ("docB", None, "COD456", "Altro pezzo", None),
    ]
    items = build_plumbing_items(rows)
    assert len(items) == 2
    assert isinstance(items[0], EvalItem)
    assert items[0].id == "plumb_docA_COD123"
    assert items[0].query == "COD123"
    assert items[0].lang == "it"
    assert items[0].branch == "structured"
    assert items[0].doc == "docA"
    assert items[0].page == 10
    # The expected could just combine them as string
    assert items[0].expected == "COD123 Pezzo di ferro 100.5"
    assert items[0].source == "plumbing"

    # Second item with None price and page
    assert items[1].id == "plumb_docB_COD456"
    assert items[1].expected == "COD456 Altro pezzo"
    assert items[1].page is None

def test_build_coverage_row():
    structured = {"docA", "docB"}
    narrative = {"docA", "docC"}
    visual = {"docA"}
    
    rowA = build_coverage_row("docA", structured, narrative, visual)
    assert rowA == {"doc": "docA", "structured": True, "narrative": True, "visual": True, "indexed": True}
    
    rowB = build_coverage_row("docB", structured, narrative, visual)
    assert rowB == {"doc": "docB", "structured": True, "narrative": False, "visual": False, "indexed": True}

    rowD = build_coverage_row("docD", structured, narrative, visual)
    assert rowD == {"doc": "docD", "structured": False, "narrative": False, "visual": False, "indexed": False}
