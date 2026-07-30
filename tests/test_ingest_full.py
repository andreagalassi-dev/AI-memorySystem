# tests/test_ingest_full.py
from src.ingestion.ingest_full import ingest_document_full, IngestReport

def _ok(n):        # fabbrica una BranchFn che ritorna n
    return lambda: n
def _boom(msg):    # fabbrica una BranchFn che solleva
    def f(): raise RuntimeError(msg)
    return f

def test_tutti_i_binari_ok_aggrega_i_conteggi():
    r = ingest_document_full("mamoli", structured=_ok(151), narrative=_ok(40), visual=_ok(18))
    assert (r.doc_stem, r.structured_rows, r.narrative_chunks, r.visual_pages) == ("mamoli", 151, 40, 18)
    assert r.scarti == {}
    assert set(r.timings) == {"structured", "narrative", "visual"}

def test_un_binario_fallito_non_ferma_gli_altri():
    r = ingest_document_full("nsk", structured=_boom("docling giu'"), narrative=_ok(30), visual=_ok(12))
    assert r.structured_rows == 0            # fallito -> resta 0
    assert r.narrative_chunks == 30 and r.visual_pages == 12   # gli altri proseguono
    assert "structured" in r.scarti and "docling giu'" in r.scarti["structured"]
    assert "narrative" not in r.scarti and "visual" not in r.scarti

def test_report_e_una_dataclass_serializzabile():
    r = ingest_document_full("x", structured=_ok(1), narrative=_ok(1), visual=_ok(1))
    assert isinstance(r, IngestReport)
