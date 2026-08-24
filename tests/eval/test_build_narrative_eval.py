# tests/eval/test_build_narrative_eval.py
"""Test del generatore del metro narrativo (`scripts/build_narrative_eval.py`).

Copre due livelli:
1. Logica pura (`_dedup`, `_find_twins`) — nessun I/O, gira ovunque.
2. Invarianti sull'OUTPUT gia' committato (`data/eval/eval_narrative_v1_1.jsonl`
   + il suo `_manifest.json`) — regressione statica, nessuna rete/Qdrant
   necessaria: se qualcuno rigenera il file con un bug, questi test lo colgono
   senza dover rilanciare la risoluzione via indice.
"""
import json
from pathlib import Path

from scripts.build_narrative_eval import _dedup, _find_twins

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. Logica pura
# ---------------------------------------------------------------------------

def _row(qa_id, query, note=""):
    return {"qa_id": qa_id, "query": query, "lang": "IT", "query_type": "natural",
            "expected": [{"code": "X"}], "note": note}


def test_dedup_no_duplicates_passthrough():
    rows = [("eval_set_v0", _row("Q1", "prima query")),
            ("eval_set_v0", _row("Q2", "seconda query"))]
    deduped, log = _dedup(rows)
    assert [r["qa_id"] for _, r in deduped] == ["Q1", "Q2"]
    assert log == []


def test_dedup_keeps_last_occurrence_v1_over_v0():
    rows = [("eval_set_v0", _row("Q033", "mi serve un movimento ceramico di ricambio")),
            ("eval_set_v1", _row("C006", "mi serve un movimento ceramico di ricambio"))]
    deduped, log = _dedup(rows)
    assert len(deduped) == 1
    src, row = deduped[0]
    assert (src, row["qa_id"]) == ("eval_set_v1", "C006")
    assert len(log) == 1
    assert log[0]["kept"] == {"set": "eval_set_v1", "qa_id": "C006"}
    assert log[0]["dropped"] == {"set": "eval_set_v0", "qa_id": "Q033"}


def test_dedup_preserves_order_of_first_occurrence():
    """Il posto nell'output segue la prima apparizione della query, il
    CONTENUTO segue l'ultima: cosi' l'ordine resta stabile anche quando la
    correzione arriva da una fonte successiva."""
    rows = [("eval_set_v0", _row("Q1", "alfa")),
            ("eval_set_v0", _row("Q2", "beta")),
            ("eval_set_v1", _row("C1", "alfa"))]
    deduped, _ = _dedup(rows)
    assert [r["qa_id"] for _, r in deduped] == ["C1", "Q2"]


def test_dedup_query_text_is_stripped_before_comparison():
    rows = [("eval_set_v0", _row("Q1", "query con spazi ")),
            ("eval_set_v1", _row("C1", " query con spazi"))]
    deduped, log = _dedup(rows)
    assert len(deduped) == 1
    assert len(log) == 1


def test_find_twins_excludes_expected_codes():
    catalog = [{"doc": "d", "page": 5, "code": "A", "desc_search": "vite"},
               {"doc": "d", "page": 6, "code": "B", "desc_search": "vite"}]
    twins = _find_twins(catalog, expected_codes={"A"}, target_pairs={("d", 1)})
    assert twins == [{"doc": "d", "page": 6, "code": "B"}]


def test_find_twins_excludes_pages_already_in_target():
    catalog = [{"doc": "d", "page": 5, "code": "B", "desc_search": "vite"}]
    twins = _find_twins(catalog, expected_codes=set(), target_pairs={("d", 5)})
    assert twins == []


def test_find_twins_dedups_and_sorts():
    catalog = [{"doc": "d", "page": 9, "code": "Z", "desc_search": "x"},
               {"doc": "d", "page": 9, "code": "Z", "desc_search": "x"},  # duplicato
               {"doc": "d", "page": 2, "code": "A", "desc_search": "x"}]
    twins = _find_twins(catalog, expected_codes=set(), target_pairs=set())
    assert twins == [{"doc": "d", "page": 2, "code": "A"},
                      {"doc": "d", "page": 9, "code": "Z"}]


def test_find_twins_ignores_rows_missing_fields():
    catalog = [{"doc": "d", "page": None, "code": "A", "desc_search": "x"},
               {"doc": "d", "page": 3, "code": None, "desc_search": "x"}]
    twins = _find_twins(catalog, expected_codes=set(), target_pairs=set())
    assert twins == []


# ---------------------------------------------------------------------------
# 2. Invarianti sull'output committato (statiche, senza DB)
# ---------------------------------------------------------------------------

def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.open(encoding="utf-8") if l.strip()]


def test_v1_1_output_has_no_duplicate_queries():
    out = ROOT / "data" / "eval" / "eval_narrative_v1_1.jsonl"
    items = _load_jsonl(out)
    queries = [it["query"].strip() for it in items]
    assert len(queries) == len(set(queries)), (
        "il set v1.1 non deve contenere due item con la stessa query esatta")


def test_v1_1_output_targets_consistent_per_query():
    """Ridondante col test precedente finche' le query sono uniche, ma
    protegge anche se in futuro la stessa query comparisse in due item con
    provenienza diversa: i bersagli devono coincidere."""
    out = ROOT / "data" / "eval" / "eval_narrative_v1_1.jsonl"
    items = _load_jsonl(out)
    by_query: dict[str, list] = {}
    for it in items:
        by_query.setdefault(it["query"].strip(), []).append(it["targets"])
    for query, target_lists in by_query.items():
        first = target_lists[0]
        for other in target_lists[1:]:
            assert other == first, f"target divergenti per la query {query!r}"


def test_v1_1_manifest_counts_match_emitted_lines():
    out_dir = ROOT / "data" / "eval"
    manifest = json.loads((out_dir / "eval_narrative_v1_1_manifest.json")
                           .read_text(encoding="utf-8"))
    kept = _load_jsonl(out_dir / "eval_narrative_v1_1.jsonl")
    excluded = _load_jsonl(out_dir / "eval_narrative_v1_1_excluded.jsonl")
    assert manifest["n_kept"] == len(kept)
    assert manifest["n_excluded"] == len(excluded)
    dedup_log = json.loads((out_dir / "eval_narrative_v1_1_dedup_log.json")
                            .read_text(encoding="utf-8"))
    assert manifest["n_dedup_dropped"] == len(dedup_log)


def test_v1_1_does_not_overwrite_historic_v1():
    """Il file storico v1 (56 item kept) deve restare quello con cui sono
    stati misurati i numeri passati (12/56, 18/56, R@10 0.625)."""
    out_dir = ROOT / "data" / "eval"
    v1 = _load_jsonl(out_dir / "eval_narrative_v1.jsonl")
    v1_1 = _load_jsonl(out_dir / "eval_narrative_v1_1.jsonl")
    assert len(v1) == 56, "il file storico v1 non va rigenerato/alterato"
    assert len(v1_1) != len(v1), (
        "v1.1 deve dichiarare esplicitamente un conteggio diverso da v1, mai "
        "coincidere per caso senza che nessuno lo verifichi")


def test_v1_1_fixes_reversed_case_aeratore():
    """Il caso rovesciato: la query 'aeratore 24x1 di ricambio' non deve piu'
    restare nel set kept con il target stale (v0/Q035, expected stretto)."""
    out_dir = ROOT / "data" / "eval"
    kept = _load_jsonl(out_dir / "eval_narrative_v1_1.jsonl")
    assert not any(it["query"].strip() == "aeratore 24x1 di ricambio" for it in kept), (
        "la versione corretta (C007, expected allargato) risolve a un codice "
        "ambiguo tra due documenti e non ha un target curato: la query deve "
        "uscire dal set, non restarci con il target vecchio")
