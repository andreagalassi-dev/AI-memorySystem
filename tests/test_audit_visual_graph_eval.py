import sys
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest
from scripts.audit_visual_graph_eval import calc_metrics, get_rank

def test_get_rank():
    # Hit at rank 1
    assert get_rank([("docA", 1), ("docB", 2)], [("docA", 1)]) == 1
    # Hit at rank 2
    assert get_rank([("docA", 1), ("docB", 2)], [("docB", 2)]) == 2
    # No hit
    assert get_rank([("docA", 1), ("docB", 2)], [("docC", 3)]) is None

def test_calc_metrics():
    # Ranks: 1, 2, None, 4
    # recall@1 = 1/4 = 0.25
    # recall@3 = 2/4 = 0.5
    # recall@5 = 3/4 = 0.75
    # mrr = (1/1 + 1/2 + 0 + 1/4) / 4 = 1.75 / 4 = 0.4375
    ranks = [1, 2, None, 4]
    m = calc_metrics(ranks)
    assert m["recall@1"] == 0.25
    assert m["recall@3"] == 0.50
    assert m["recall@5"] == 0.75
    assert m["mrr"] == 0.4375

    # Empty
    assert calc_metrics([]) == {}

def test_per_language_metrics():
    langs = ["IT", "EN", "IT", "DE"]
    ranks = [1, 2, None, 1]
    
    # IT ranks: 1, None
    # IT recall@1 = 0.5, recall@3 = 0.5, mrr = 0.5
    it_ranks = [r for r, l in zip(ranks, langs) if l == "IT"]
    m_it = calc_metrics(it_ranks)
    assert m_it["recall@1"] == 0.5
    assert m_it["mrr"] == 0.5

    # EN ranks: 2
    # EN recall@1 = 0.0, recall@3 = 1.0, mrr = 0.5
    en_ranks = [r for r, l in zip(ranks, langs) if l == "EN"]
    m_en = calc_metrics(en_ranks)
    assert m_en["recall@1"] == 0.0
    assert m_en["recall@3"] == 1.0
    assert m_en["mrr"] == 0.5
