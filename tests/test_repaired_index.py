"""Debito §4 follow-up — i codici riparati ARRIVANO su Qdrant, cappati.

Integrazione sulla collezione reale `aike_chunks` (skip se Qdrant giu' o
non ancora re-ingestata): i 3 codici P231 recuperati dal repair pass devono
esistere come punti con warning `fuzzy_code` — la condizione che il
calcolatore di confidenza usa per non dare mai ALTA su un codice riparato.
"""

from __future__ import annotations

import pytest
from qdrant_client import models

from tests.conftest import needs_qdrant

pytestmark = needs_qdrant

COLLECTION = "aike_chunks"
# P231 (spike, ancore del normalizzatore) — GT: tutti e tre esistono nel PDF
P231_REPAIRED = ["V09275000001", "V09409100007", "V09409400000"]


def _points_by_code(code: str):
    from src.backend.clients.qdrant_client import get_qdrant_client
    pts, _ = get_qdrant_client().scroll(
        COLLECTION, limit=10, with_payload=True, with_vectors=False,
        scroll_filter=models.Filter(must=[models.FieldCondition(
            key="code", match=models.MatchValue(value=code))]))
    return pts


@pytest.fixture(scope="module", autouse=True)
def _collection_reingestata():
    """Skip esplicito se la collezione precede la fix (niente falsi rossi)."""
    if not _points_by_code(P231_REPAIRED[0]):
        pytest.skip("aike_chunks non contiene i riparati: rieseguire "
                    "scripts/ingest_chunks.py (+ backfill_qdrant.py)")


class TestRiparatiSuQdrant:
    @pytest.mark.parametrize("code", P231_REPAIRED)
    def test_riparato_p231_presente_e_cappato(self, code):
        pts = _points_by_code(code)
        assert pts, f"codice riparato {code} assente da {COLLECTION}"
        for p in pts:
            assert "fuzzy_code" in (p.payload.get("warnings") or []), \
                f"{code}: senza fuzzy_code il guardrail non lo capperebbe"
            assert p.payload["status"] == "repaired"
            assert p.payload["code"] == code

    def test_mai_riparati_senza_warning_in_collezione(self):
        """Invariante globale: status=repaired implica warning fuzzy_code."""
        from src.backend.clients.qdrant_client import get_qdrant_client
        pts, _ = get_qdrant_client().scroll(
            COLLECTION, limit=1000, with_payload=True, with_vectors=False,
            scroll_filter=models.Filter(must=[models.FieldCondition(
                key="status", match=models.MatchValue(value="repaired"))]))
        assert pts, "nessun punto repaired: re-ingestion incompleta"
        for p in pts:
            assert "fuzzy_code" in (p.payload.get("warnings") or []), p.id
