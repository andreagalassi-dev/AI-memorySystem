"""Fase B (B2/B3) — Ingestion per-documento: punti in Qdrant + riga versione + idempotenza.

Integrazione: richiede Postgres e Qdrant del compose (marker `needs_pg`/`needs_qdrant`)
e carica i modelli reali (BGE-M3 + BM25, gia' in cache). Per NON toccare la collezione
di produzione `aike_chunks`, ingerisce in una collezione dedicata usa-e-getta.

L'estrazione Docling e' INIETTATA (`extract_fn`) con markdown deterministico: il test
esercita chunking -> embedding -> upsert -> registro senza dipendere dall'env pesante.
"""

from __future__ import annotations

import uuid

import pytest
from qdrant_client import models

from tests.conftest import needs_pg, needs_qdrant

# Tabella con colonna-codice riconoscibile dal profilo `default` (passthrough).
_MD_V1 = {
    1: (
        "| Codice | Descrizione | Q.ta |\n"
        "| --- | --- | --- |\n"
        "| ABC-123 | Guarnizione di tenuta in gomma | 2 |\n"
        "| ABC-124 | Vite a testa esagonale acciaio | 4 |\n"
        "| ABC-125 | Rondella piana zincata standard | 1 |\n"
    )
}
# Versione MODIFICATA: una riga in meno + descrizione cambiata -> chunk_id diversi
# per la riga sparita: senza pulizia lascerebbe un punto orfano.
_MD_V2 = {
    1: (
        "| Codice | Descrizione | Q.ta |\n"
        "| --- | --- | --- |\n"
        "| ABC-123 | Guarnizione di tenuta rinforzata | 2 |\n"
        "| ABC-124 | Vite a testa esagonale acciaio | 4 |\n"
    )
}


def _count_stem(qc, collection: str, doc_stem: str) -> int:
    return qc.count(collection, exact=True, count_filter=models.Filter(must=[
        models.FieldCondition(key="doc_stem",
                              match=models.MatchValue(value=doc_stem))])).count


@needs_qdrant
@needs_pg
class TestIngestOne:
    @pytest.fixture()
    def collection(self):
        """Collezione Qdrant dedicata: creata dalla funzione, distrutta a fine test."""
        from src.backend.clients.qdrant_client import get_qdrant_client
        qc = get_qdrant_client()
        name = f"test_ingest_{uuid.uuid4().hex[:8]}"
        yield name
        if qc.collection_exists(name):
            qc.delete_collection(name)

    def _schema(self, conn):
        from src.registry.registry import init_schema
        init_schema(conn)

    def test_import(self):
        from src.ingestion.ingest_one import ingest_document  # noqa: F401

    def test_ingest_crea_punti_e_riga_versione(self, conn, collection):
        """B2: dopo ingest_document la collezione ha punti col doc_stem e Postgres la riga."""
        from src.backend.clients.qdrant_client import get_qdrant_client
        from src.ingestion.ingest_one import ingest_document
        from src.registry.registry import get_current_version
        self._schema(conn)
        stem = "brand_x_listino_2026"

        res = ingest_document("/finto/path.pdf", doc_stem=stem, role="tecnico",
                              conn=conn, cat="spare_parts", collection=collection,
                              extract_fn=lambda fp: _MD_V1)

        # --- Qdrant: punti presenti col doc_stem e payload RBAC/versioning ---
        qc = get_qdrant_client()
        assert res["n_points"] > 0
        assert _count_stem(qc, collection, stem) == res["n_points"]
        pts, _ = qc.scroll(collection, limit=50, with_payload=True, with_vectors=False)
        assert pts, "nessun punto upsertato"
        for p in pts:
            assert p.payload["doc_stem"] == stem
            assert p.payload["ruoli_consentiti"] == ["tecnico"]
            assert p.payload["version_status"] == "current"
            assert "page" in p.payload
        # almeno un codice della tabella e' finito nel payload (guardrail: esiste nel dato)
        codes = {p.payload.get("code") for p in pts}
        assert "ABC-123" in codes

        # --- Postgres: la versione current del documento esiste ---
        ver = get_current_version(conn, stem)
        assert ver is not None
        assert ver.version_status == "current"

    def test_reingest_non_duplica(self, conn, collection):
        """B3: re-ingerire lo stesso doc_stem con contenuto diverso non lascia orfani."""
        from src.backend.clients.qdrant_client import get_qdrant_client
        from src.ingestion.ingest_one import ingest_document
        self._schema(conn)
        stem = "brand_x_listino_2026"
        qc = get_qdrant_client()

        r1 = ingest_document("/finto/path.pdf", doc_stem=stem, role="tecnico",
                             conn=conn, cat="spare_parts", collection=collection,
                             extract_fn=lambda fp: _MD_V1)
        assert _count_stem(qc, collection, stem) == r1["n_points"]

        r2 = ingest_document("/finto/path.pdf", doc_stem=stem, role="tecnico",
                             conn=conn, cat="spare_parts", collection=collection,
                             is_update=True, extract_fn=lambda fp: _MD_V2)
        # il conteggio e' quello dell'ULTIMA ingestione, non la somma
        assert _count_stem(qc, collection, stem) == r2["n_points"]
        assert r2["n_points"] < r1["n_points"]

    def test_altri_doc_non_toccati(self, conn, collection):
        """La pulizia anti-orfani filtra su doc_stem: gli altri documenti restano."""
        from src.backend.clients.qdrant_client import get_qdrant_client
        from src.ingestion.ingest_one import ingest_document
        self._schema(conn)
        qc = get_qdrant_client()

        ingest_document("/a.pdf", doc_stem="doc_a", role="tecnico", conn=conn,
                        cat="spare_parts", collection=collection,
                        extract_fn=lambda fp: _MD_V1)
        n_a = _count_stem(qc, collection, "doc_a")
        # ingerire doc_b (con la sua pulizia) non deve toccare doc_a
        ingest_document("/b.pdf", doc_stem="doc_b", role="tecnico", conn=conn,
                        cat="spare_parts", collection=collection,
                        extract_fn=lambda fp: _MD_V2)
        assert _count_stem(qc, collection, "doc_a") == n_a
        assert _count_stem(qc, collection, "doc_b") > 0
