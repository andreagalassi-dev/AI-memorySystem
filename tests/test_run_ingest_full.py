"""Unit test per le funzioni PURE di scripts/run_ingest_full.py (Epica C.3 runner).

Solo funzioni pure: nessun modello, nessun DB, nessuna rete."""

from __future__ import annotations

from scripts.run_ingest_full import partition_giants, pending


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
