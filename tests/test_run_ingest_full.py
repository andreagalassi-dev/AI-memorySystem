"""Unit test per le funzioni PURE di scripts/run_ingest_full.py (Epica C.3 runner).

Solo funzioni pure: nessun modello, nessun DB, nessuna rete."""

from __future__ import annotations

from types import SimpleNamespace

from scripts.run_ingest_full import (
    branch_coverage,
    filter_by_coverage,
    partition_giants,
    pdf_page_count,
    resolve_pages,
    pending,
    pending_branches,
    resolve_branches,
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


def test_partition_giants_pagine_sconosciute_e_gigante_fail_closed():
    """REGRESSIONE: pagine sconosciute NON sono "piccolo".

    Il census copre solo i documenti gia' indicizzati; per gli altri il conteggio
    valeva 0 e 0 finiva fra i normali, cioe' dentro il default `--exclude-giants`.
    Ora l'ignoto e' fail-closed: va fra i giganti."""
    normali, giganti = partition_giants(["q"], {}, 100)
    assert normali == []
    assert giganti == ["q"]

    # 0 esplicito e valori negativi: stessa sorte.
    assert partition_giants(["a", "b"], {"a": 0, "b": -1}, 100) == ([], ["a", "b"])


def test_resolve_pages_fallback_su_stem_assente_dal_census():
    """IL CASO DEI 28: stem assente dal census -> pagine lette dal PDF reale.

    Senza fallback `bgr` pesava 0 pagine ed entrava fra i "normali" di un run
    prudente; con il fallback pesa 456 ed e' correttamente un gigante."""
    reale = {"bgr": 456, "sew": 4}
    pages = resolve_pages(["bgr", "sew"], {}, lambda st: reale[st])
    assert pages == {"bgr": 456, "sew": 4}

    normali, giganti = partition_giants(["bgr", "sew"], pages, 100)
    assert normali == ["sew"]
    assert giganti == ["bgr"]


def test_resolve_pages_il_census_ha_la_precedenza_e_non_apre_pdf():
    """Lo stem censito usa il census; il fallback non viene nemmeno chiamato."""
    chiamate: list[str] = []

    def counter(stem: str) -> int:
        chiamate.append(stem)
        return 999

    pages = resolve_pages(["x", "y"], {"x": 30}, counter)
    assert pages == {"x": 30, "y": 999}
    assert chiamate == ["y"]  # mai chiamato per "x"


def test_resolve_pages_pdf_illeggibile_resta_sconosciuto_e_finisce_fra_i_giganti():
    """Se nemmeno il PDF si legge, il conteggio resta 0 e il fail-closed scatta."""
    pages = resolve_pages(["rotto"], {}, lambda st: 0)
    assert pages == {"rotto": 0}
    assert partition_giants(["rotto"], pages, 100) == ([], ["rotto"])


def test_pdf_page_count_path_mancante_o_illeggibile_ritorna_zero():
    assert pdf_page_count(None) == 0
    assert pdf_page_count("") == 0
    assert pdf_page_count("/percorso/che/non/esiste_xyz.pdf") == 0


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


# ---------------------------------------------------------------------------
# Copertura PER-RAMO (bug C.3: l'OR globale fra righe_listino e aike_chunks
# dichiarava "coperto" un documento con SOLO lo strutturato fatto, e il ramo
# narrativo/visivo non entrava mai in coda).
# ---------------------------------------------------------------------------
CHUNKS = "aike_chunks"
VISUAL = "aike_pages_visual"


class _FakeCursor:
    """Cursore Postgres minimale: risponde alla sola SELECT su righe_listino."""

    def __init__(self, stems):
        self._stems = stems
        self._rows: list[tuple] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, *args):
        assert "righe_listino" in sql, sql
        self._rows = [(s,) for s in self._stems]

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, stems):
        self._stems = stems

    def cursor(self):
        return _FakeCursor(self._stems)


class _FakeQdrant:
    """Scroll paginato su piu' collection: {collection: [stem_del_payload, ...]}."""

    def __init__(self, per_collection, page=2):
        self._data = per_collection
        self._page = page
        self.seen: list[str] = []

    def scroll(self, collection, limit=1000, offset=None, with_payload=None,
               with_vectors=False):
        self.seen.append(collection)
        docs = self._data.get(collection)
        if docs is None:
            raise RuntimeError(f"collection inesistente: {collection}")
        start = offset or 0
        end = start + self._page
        points = [SimpleNamespace(payload={"doc": d}) for d in docs[start:end]]
        next_offset = end if end < len(docs) else None
        return points, next_offset


def _coverage(struct=(), narr=(), vis=()):
    conn = _FakeConn(list(struct))
    qc = _FakeQdrant({CHUNKS: list(narr), VISUAL: list(vis)})
    return branch_coverage(conn, qc, chunks_collection=CHUNKS,
                           visual_collection=VISUAL)


def test_branch_coverage_tiene_i_rami_separati():
    cov = _coverage(struct=["solo_struct", "tutti"], narr=["tutti"],
                    vis=["tutti", "solo_vis"])
    assert cov["structured"] == {"solo_struct", "tutti"}
    assert cov["narrative"] == {"tutti"}
    assert cov["visual"] == {"tutti", "solo_vis"}


def test_branch_coverage_legge_la_collection_visiva():
    conn = _FakeConn([])
    qc = _FakeQdrant({CHUNKS: [], VISUAL: ["v"]})
    branch_coverage(conn, qc, chunks_collection=CHUNKS, visual_collection=VISUAL)
    assert VISUAL in qc.seen, "il ramo visivo non e' stato interrogato affatto"


# --- la FIRMA del difetto: righe strutturate, ZERO chunk ---------------------
def test_doc_con_righe_ma_zero_chunk_e_pending_per_il_narrativo():
    cov = _coverage(struct=["monstre"], narr=[], vis=[])
    todo = pending_branches(["monstre"], cov, {"structured", "narrative", "visual"})
    assert "monstre" in todo, (
        "regressione: un doc con righe_listino e 0 chunk risulta 'coperto' e "
        "non entra mai in coda (bug dell'OR globale)"
    )
    assert todo["monstre"] == {"narrative", "visual"}


def test_doc_con_tutti_e_tre_i_rami_non_e_pending():
    cov = _coverage(struct=["ok"], narr=["ok"], vis=["ok"])
    assert pending_branches(["ok"], cov, {"structured", "narrative", "visual"}) == {}


def test_doc_con_narrativo_fatto_ma_visivo_no_e_pending_solo_per_il_visivo():
    cov = _coverage(struct=["x"], narr=["x"], vis=[])
    assert pending_branches(["x"], cov, {"structured", "narrative", "visual"}) == {
        "x": {"visual"}
    }


def test_nessun_documento_nessun_crash():
    cov = _coverage()
    assert cov == {"structured": set(), "narrative": set(), "visual": set()}
    assert pending_branches([], cov, {"structured", "narrative", "visual"}) == {}


def test_pending_branches_considera_solo_i_rami_richiesti():
    # --branches=narrative: lo strutturato mancante NON deve rendere pending.
    cov = _coverage(struct=[], narr=["n"], vis=[])
    assert pending_branches(["n"], cov, {"narrative"}) == {}
    assert pending_branches(["n"], cov, {"narrative", "structured"}) == {
        "n": {"structured"}
    }


def test_pending_branches_preserva_ordine_dei_candidates():
    cov = _coverage(struct=[], narr=[], vis=[])
    todo = pending_branches(["c", "a", "b"], cov, {"narrative"})
    assert list(todo) == ["c", "a", "b"]


def test_branch_coverage_non_esplode_se_una_collection_manca():
    # Qdrant giu'/collection assente: si degrada a "niente coperto", non crasha.
    conn = _FakeConn(["s"])
    qc = _FakeQdrant({})  # nessuna collection -> scroll solleva
    cov = branch_coverage(conn, qc, chunks_collection=CHUNKS,
                          visual_collection=VISUAL)
    assert cov["structured"] == {"s"}
    assert cov["narrative"] == set()
    assert cov["visual"] == set()


# --- il runner deve eseguire SOLO i rami mancanti, per documento -------------
def test_resolve_branches_usa_il_piano_per_documento():
    plan = {"a": {"narrative"}, "b": {"visual", "narrative"}}
    assert resolve_branches("a", plan, {"structured", "narrative", "visual"}) == {
        "narrative"
    }
    assert resolve_branches("b", plan, {"structured"}) == {"visual", "narrative"}


def test_resolve_branches_fallback_sul_default_se_lo_stem_non_e_nel_piano():
    assert resolve_branches("z", {}, {"structured"}) == {"structured"}
    assert resolve_branches("z", None, {"visual"}) == {"visual"}
