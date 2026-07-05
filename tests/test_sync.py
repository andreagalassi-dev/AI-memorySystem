"""P4.3 — Test del riconciliatore Qdrant<-SSOT e del flusso "nuova versione".

Vincolo sotto test: lo switch di versione e' SSOT-first (transazione PG) +
riconciliazione CONVERGENTE su Qdrant — rilanciabile alla cieca dopo
qualsiasi crash parziale, senza mai creare versioni spurie.
"""

from __future__ import annotations

import pytest

from src.common.roles import Role
from src.registry.registry import BackfillRecord

REC_V1 = BackfillRecord("superseded", 1, [Role.TECNICO])
REC_V2 = BackfillRecord("current", 2, [Role.COMMERCIALE, Role.TECNICO])


# ------------------------------------------------------------- parti pure

class TestAlignmentFilter:
    def test_ruoli_presenti_in_must_e_assenti_in_must_not(self):
        from src.registry.sync import alignment_filter
        f = alignment_filter("doc_a", REC_V2)
        must_keys = [(c.key, getattr(c.match, "value", None)) for c in f.must]
        assert ("doc", "doc_a") in must_keys
        assert ("version_status", "current") in must_keys
        assert ("doc_version", 2) in must_keys
        assert ("ruoli_consentiti", "commerciale") in must_keys
        assert ("ruoli_consentiti", "tecnico") in must_keys
        not_keys = [(c.key, c.match.value) for c in f.must_not]
        assert not_keys == [("ruoli_consentiti", "amministrazione")]


class TestStemsToPatch:
    def test_divergenti_selezionati_allineati_no(self):
        from src.registry.sync import stems_to_patch
        counts = {"doc_a": (5, 5), "doc_b": (3, 7), "doc_c": (0, 2)}
        assert stems_to_patch(counts) == ["doc_b", "doc_c"]

    def test_tutto_allineato_lista_vuota(self):
        from src.registry.sync import stems_to_patch
        assert stems_to_patch({"doc_a": (5, 5)}) == []


# ------------------------------------------------ integrazione Qdrant

from tests.conftest import needs_qdrant  # noqa: E402

TEST_COLLS = ("test_sync_tmp_a", "test_sync_tmp_b")


@pytest.fixture()
def test_colls():
    """Due collection USA-E-GETTA con payload doc + chiavi P4.2 a stato v1.

    Le collection di produzione non vengono MAI toccate dai test.
    """
    from qdrant_client import models

    from src.backend.clients.qdrant_client import get_qdrant_client
    qc = get_qdrant_client()
    for coll in TEST_COLLS:
        if qc.collection_exists(coll):
            qc.delete_collection(coll)
        qc.create_collection(coll, vectors_config=models.VectorParams(
            size=4, distance=models.Distance.COSINE))
        # facet richiede l'indice (in produzione `doc` e' indicizzato, P2)
        qc.create_payload_index(coll, field_name="doc",
                                field_schema=models.PayloadSchemaType.KEYWORD,
                                wait=True)
        qc.upsert(coll, points=[
            models.PointStruct(id=i, vector=[0.1, 0.2, 0.3, 0.4],
                               payload={"doc": stem, "page": i,
                                        "version_status": "current",
                                        "doc_version": 1,
                                        "ruoli_consentiti": ["tecnico"],
                                        "parent_id": "V09156800000" if i > 0 else None,
                                        "is_assembly": i == 0})
            for i, stem in enumerate(["stem_old", "stem_old", "stem_other"])],
            wait=True)
    yield qc
    for coll in TEST_COLLS:
        qc.delete_collection(coll)


EXPORT_AFTER_SWITCH = {
    # stem_old sostituito da stem_new (che NON ha ancora punti)
    "stem_old": BackfillRecord("superseded", 1, [Role.TECNICO]),
    "stem_new": BackfillRecord("current", 2, [Role.TECNICO]),
    "stem_other": BackfillRecord("current", 1, [Role.TECNICO]),
}


@needs_qdrant
class TestSyncCollection:
    def test_switch_v1_v2_convergente(self, test_colls):
        from src.registry.sync import sync_collection
        qc = test_colls
        rep = sync_collection(qc, TEST_COLLS[0], EXPORT_AFTER_SWITCH)
        assert rep.patched == ["stem_old"]          # stem_other gia' allineato
        assert rep.missing_stems == ["stem_new"]    # v2 senza punti: warning
        pts, _ = qc.scroll(TEST_COLLS[0], limit=10, with_payload=True)
        for p in pts:
            expect = "superseded" if p.payload["doc"] == "stem_old" else "current"
            assert p.payload["version_status"] == expect
            assert p.payload["page"] is not None    # chiavi preesistenti intatte
            if p.id == 0:
                assert p.payload["is_assembly"] is True
                assert p.payload["parent_id"] is None
            else:
                assert p.payload["is_assembly"] is False
                assert p.payload["parent_id"] == "V09156800000"

    def test_crash_a_meta_poi_rilancio_converge(self, test_colls):
        from src.registry.sync import sync_collection
        qc = test_colls
        # "crash": la patch raggiunge solo la collection A, la B resta v1
        sync_collection(qc, TEST_COLLS[0], EXPORT_AFTER_SWITCH)
        # rilancio alla cieca su TUTTE: A no-op, B converge
        rep_a = sync_collection(qc, TEST_COLLS[0], EXPORT_AFTER_SWITCH)
        rep_b = sync_collection(qc, TEST_COLLS[1], EXPORT_AFTER_SWITCH)
        assert rep_a.patched == [] and rep_a.skipped == 2
        assert rep_b.patched == ["stem_old"]
        # terzo run: tutto fermo (idempotenza osservabile)
        assert sync_collection(qc, TEST_COLLS[1], EXPORT_AFTER_SWITCH).patched == []

    def test_orfano_fail_closed(self, test_colls):
        from src.registry.sync import sync_collection
        export_senza_other = {k: v for k, v in EXPORT_AFTER_SWITCH.items()
                              if k != "stem_other"}
        with pytest.raises(ValueError, match="stem_other"):
            sync_collection(test_colls, TEST_COLLS[0], export_senza_other)


# --------------------------------------- flusso "nuova versione" (orchestratore)

from tests.conftest import needs_pg  # noqa: E402


class FakeSync:
    """Registra le chiamate di riconciliazione (pattern Deps del grafo)."""

    def __init__(self, missing: list[str] | None = None):
        self.calls: list[dict] = []
        self.missing = missing or []

    def __call__(self, export):
        from src.registry.sync import SyncReport
        self.calls.append(dict(export))
        return [SyncReport("fake_coll", patched=[], skipped=0,
                           missing_stems=self.missing)]


@needs_pg
class TestSupersedeDocument:
    def _setup(self, conn):
        from src.registry.registry import init_schema, register_document
        init_schema(conn)
        register_document(conn, slug="mamoli", category="original_dataset",
                          roles=[Role.TECNICO], doc_stem="mamoli_2021",
                          langs=["it"])

    def test_switch_completo(self, conn):
        from src.registry.registry import get_current_version, supersede_document
        self._setup(conn)
        sync = FakeSync()
        rep = supersede_document(conn, slug="mamoli", new_doc_stem="mamoli_2023",
                                 langs=["it"], sync=sync)
        assert rep.phase1 == "switched"
        cur = get_current_version(conn, "mamoli")
        assert cur.doc_stem == "mamoli_2023" and cur.version_no == 2
        assert len(sync.calls) == 1
        assert set(sync.calls[0]) == {"mamoli_2021", "mamoli_2023"}

    def test_rilancio_post_crash_non_crea_v3(self, conn):
        from src.registry.registry import get_current_version, supersede_document
        self._setup(conn)
        supersede_document(conn, slug="mamoli", new_doc_stem="mamoli_2023",
                           langs=["it"], sync=FakeSync())
        # "crash" dopo la fase 1: rilancio ALLA CIECA dello stesso comando
        sync2 = FakeSync()
        rep2 = supersede_document(conn, slug="mamoli", new_doc_stem="mamoli_2023",
                                  langs=["it"], sync=sync2)
        assert rep2.phase1 == "already-done"
        cur = get_current_version(conn, "mamoli")
        assert cur.version_no == 2                    # nessuna v3
        assert len(sync2.calls) == 1                  # la sync riparte comunque

    def test_stem_di_altro_documento_rifiutato(self, conn):
        from src.registry.registry import register_document, supersede_document
        self._setup(conn)
        register_document(conn, slug="kohler", category="engines",
                          roles=[Role.TECNICO], doc_stem="kohler_tp7120",
                          langs=["en"])
        sync = FakeSync()
        with pytest.raises(ValueError, match="kohler_tp7120"):
            supersede_document(conn, slug="mamoli", new_doc_stem="kohler_tp7120",
                               langs=["it"], sync=sync)
        assert sync.calls == []                       # fail-closed: niente sync

    def test_warning_se_v2_senza_punti(self, conn):
        from src.registry.registry import supersede_document
        self._setup(conn)
        rep = supersede_document(conn, slug="mamoli", new_doc_stem="mamoli_2023",
                                 langs=["it"],
                                 sync=FakeSync(missing=["mamoli_2023"]))
        assert any("mamoli_2023" in w for w in rep.warnings)
