"""Unit test per le funzioni PURE di scripts/run_ingest_full.py (Epica C.3 runner).

Solo funzioni pure: nessun modello, nessun DB, nessuna rete."""

from __future__ import annotations

from scripts.run_ingest_full import (
    filter_by_coverage,
    partition_giants,
    pending,
    select_branches,
)


def test_pending_salta_coperti():
    assert pending(["a", "b", "c"], {"b"}) == ["a", "c"]


def test_pending_ordine_preservato():
    # L'ordine dei candidates e' preservato, i coperti rimossi.
    assert pending(["c", "a", "b", "d"], {"a", "d"}) == ["c", "b"]


def test_pending_vuoto_se_tutti_coperti():
    assert pending(["a", "b", "c"], {"a", "b", "c"}) == []


def test_pending_nessun_coperto():
    assert pending(["a", "b"], set()) == ["a", "b"]


def test_partition_giants():
    normali, giganti = partition_giants(
        ["x", "y", "z"], {"x": 998, "y": 10, "z": 150}, 100
    )
    assert normali == ["y"]
    assert giganti == ["x", "z"]


def test_partition_giants_soglia_inclusiva():
    # threshold e' inclusiva per i normali: pages == threshold -> normale.
    normali, giganti = partition_giants(["a", "b"], {"a": 100, "b": 101}, 100)
    assert normali == ["a"]
    assert giganti == ["b"]


def test_partition_giants_stem_senza_census_e_normale():
    # doc_pages default 0 -> non gigante.
    normali, giganti = partition_giants(["q"], {}, 100)
    assert normali == ["q"]
    assert giganti == []


# ---------------------------------------------------------------------------
# --force: bypassa il filtro copertura
# ---------------------------------------------------------------------------
def test_filter_by_coverage_senza_force_salta_coperti():
    # force=False -> comportamento storico: rimuove i coperti.
    assert filter_by_coverage(["a", "b", "c"], {"b"}, force=False) == ["a", "c"]


def test_filter_by_coverage_con_force_passa_stem_coperto():
    # force=True -> uno stem COPERTO passa comunque (candidates inalterati).
    assert filter_by_coverage(["a", "b", "c"], {"b"}, force=True) == ["a", "b", "c"]


def test_filter_by_coverage_force_su_tutti_coperti():
    # Anche se TUTTI coperti, force mantiene i candidates.
    assert filter_by_coverage(["x", "y"], {"x", "y"}, force=True) == ["x", "y"]


# ---------------------------------------------------------------------------
# --branches: rami non selezionati -> no-op (lambda: 0)
# ---------------------------------------------------------------------------
def _sentinel_branches():
    return {
        "structured": lambda: 111,
        "narrative": lambda: 222,
        "visual": lambda: 333,
    }


def test_select_branches_solo_narrative_visual_azzera_structured():
    out = select_branches(_sentinel_branches(), {"narrative", "visual"})
    # lo strutturato diventa no-op -> 0 (NON chiama run_document_massive)
    assert out["structured"]() == 0
    # gli altri restano quelli reali
    assert out["narrative"]() == 222
    assert out["visual"]() == 333
    # chiavi preservate
    assert set(out) == {"structured", "narrative", "visual"}


def test_select_branches_tutti_selezionati_lascia_intatto():
    out = select_branches(
        _sentinel_branches(), {"structured", "narrative", "visual"}
    )
    assert out["structured"]() == 111
    assert out["narrative"]() == 222
    assert out["visual"]() == 333


def test_select_branches_nessuno_selezionato_tutto_noop():
    out = select_branches(_sentinel_branches(), set())
    assert out["structured"]() == 0
    assert out["narrative"]() == 0
    assert out["visual"]() == 0
