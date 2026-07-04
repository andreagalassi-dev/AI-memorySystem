"""P4.4 — Test del query_filter version-aware su search() (read-only).

Vincolo sotto test (contratto handoff §5.3): il filtro limita i CANDIDATI,
non tocca lo scoring. Integrazione in sola lettura sulla collection di
produzione: oggi tutto e' current, quindi filtro current == nessun filtro
e filtro superseded == zero hit (prova che il filtro morde).
"""

from __future__ import annotations

from tests.conftest import needs_qdrant

QUERY = "guarnizione della pompa idraulica"


class TestVersionFilterPuro:
    def test_costruzione(self):
        from src.retrieval.search import version_filter
        f = version_filter("current")
        assert f.must[0].key == "version_status"
        assert f.must[0].match.value == "current"


@needs_qdrant
class TestSearchConFiltro:
    def _client(self):
        from src.backend.clients.qdrant_client import get_qdrant_client
        return get_qdrant_client()

    def test_superseded_zero_hit_oggi(self):
        from src.retrieval.search import search, version_filter
        hits = search(self._client(), QUERY, "rrf", limit=5,
                      query_filter=version_filter("superseded"))
        assert hits == []

    def test_current_uguale_a_nessun_filtro(self):
        # tutto il corpus e' current: il filtro non deve cambiare i risultati
        from src.retrieval.search import search, version_filter
        qc = self._client()
        base = [(h.id, round(h.score, 6)) for h in search(qc, QUERY, "sparse", limit=5)]
        filt = [(h.id, round(h.score, 6))
                for h in search(qc, QUERY, "sparse", limit=5,
                                query_filter=version_filter("current"))]
        assert filt == base

    def test_filtro_su_strategia_ibrida(self):
        from src.retrieval.search import search, version_filter
        hits = search(self._client(), QUERY, "rrf", limit=5,
                      query_filter=version_filter("current"))
        assert hits and all(h.payload["version_status"] == "current" for h in hits)
