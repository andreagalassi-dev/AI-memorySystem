"""P4.1 — Test del registro versioni su PostgreSQL (SSOT) e dei ruoli tipizzati.

Vincolo sotto test (SPEC §E, §F.3): Postgres e' la fonte di verita' di
versioni e ruoli; il ruolo e' un tipo chiuso (mai None, mai stringa libera).
I test di integrazione richiedono il Postgres del compose (skip se giu'),
stesso pattern needs_data di test_chunking.py.
"""

from __future__ import annotations

import pytest

from src.common.roles import Role, parse_role

# ------------------------------------------------------------------ Role

class TestParseRole:
    def test_valori_canonici(self):
        assert parse_role("commerciale") is Role.COMMERCIALE
        assert parse_role("tecnico") is Role.TECNICO
        assert parse_role("amministrazione") is Role.AMMINISTRAZIONE

    def test_normalizza_maiuscole_e_spazi(self):
        assert parse_role("  Tecnico ") is Role.TECNICO

    def test_none_rifiutato(self):
        with pytest.raises(TypeError):
            parse_role(None)  # type: ignore[arg-type]

    def test_non_stringa_rifiutato(self):
        with pytest.raises(TypeError):
            parse_role(42)  # type: ignore[arg-type]

    def test_valore_ignoto_rifiutato(self):
        with pytest.raises(ValueError):
            parse_role("admin")

    def test_stringa_vuota_rifiutata(self):
        with pytest.raises(ValueError):
            parse_role("")

    def test_role_e_il_suo_valore_stringa(self):
        # StrEnum: serializzabile diretto nel payload/SQL
        assert Role.TECNICO == "tecnico"


# ------------------------------------------------------- seed ruoli default

class TestSeedRuoli:
    MAPPING = {
        "price_lists": [Role.COMMERCIALE, Role.TECNICO, Role.AMMINISTRAZIONE],
        "engines": [Role.TECNICO],
        "_fallback": [Role.TECNICO],
    }

    def test_categoria_nota(self):
        from src.registry.seed import roles_for_category
        assert roles_for_category("engines", self.MAPPING) == [Role.TECNICO]

    def test_categoria_ignota_usa_fallback(self):
        from src.registry.seed import roles_for_category
        assert roles_for_category("categoria_nuova", self.MAPPING) == [Role.TECNICO]

    def test_categoria_none_usa_fallback(self):
        from src.registry.seed import roles_for_category
        assert roles_for_category(None, self.MAPPING) == [Role.TECNICO]

    def test_load_valida_i_ruoli(self, tmp_path):
        from src.registry.seed import load_default_roles
        bad = tmp_path / "roles.json"
        bad.write_text('{"engines": ["admin"], "_fallback": ["tecnico"]}')
        with pytest.raises(ValueError):
            load_default_roles(bad)

    def test_load_richiede_fallback(self, tmp_path):
        from src.registry.seed import load_default_roles
        nofb = tmp_path / "roles.json"
        nofb.write_text('{"engines": ["tecnico"]}')
        with pytest.raises(ValueError):
            load_default_roles(nofb)

    def test_config_di_progetto_rispetta_la_decisione(self):
        # Decisione P4.1: tecnico vede tutto; commerciale/amministrazione solo
        # price_lists + corporate_brochures.
        from src.registry.seed import DEFAULT_ROLES_PATH, load_default_roles
        mapping = load_default_roles(DEFAULT_ROLES_PATH)
        for cat, roles in mapping.items():
            assert Role.TECNICO in roles, f"{cat} non include tecnico"
            if cat not in ("price_lists", "corporate_brochures", "_fallback"):
                assert roles == [Role.TECNICO], f"{cat} non deve esporre altri ruoli"
        for cat in ("price_lists", "corporate_brochures"):
            assert set(mapping[cat]) == set(Role), f"{cat} deve includere tutti i ruoli"


class TestAggregateDocs:
    def test_aggrega_cat_e_lingue_per_doc(self):
        from src.registry.seed import aggregate_docs
        payloads = [
            {"doc": "mamoli", "cat": "original_dataset", "lang": "it"},
            {"doc": "mamoli", "cat": "original_dataset", "lang": "en"},
            {"doc": "kohler", "cat": "engines", "lang": "en"},
        ]
        agg = aggregate_docs(payloads)
        assert agg["mamoli"].category == "original_dataset"
        assert agg["mamoli"].langs == ["en", "it"]  # ordinate, senza duplicati
        assert agg["kohler"].langs == ["en"]

    def test_cat_e_lang_mancanti_tollerati(self):
        # sul lato testo cat/lang possono essere None: non devono rompere il seed
        from src.registry.seed import aggregate_docs
        agg = aggregate_docs([{"doc": "x", "cat": None, "lang": None}])
        assert agg["x"].category is None
        assert agg["x"].langs == []

    def test_doc_mancante_scartato(self):
        from src.registry.seed import aggregate_docs
        assert aggregate_docs([{"cat": "engines"}]) == {}


# ------------------------------------------------- integrazione PostgreSQL

def _pg_available() -> bool:
    try:
        import psycopg

        from src.backend.clients.postgres_client import conninfo
        with psycopg.connect(conninfo(), connect_timeout=2):
            return True
    except Exception:
        return False


needs_pg = pytest.mark.skipif(
    not _pg_available(),
    reason="richiede il Postgres del compose (docker compose up -d postgres)",
)


@pytest.fixture()
def conn():
    """Connessione su schema temporaneo: il registro reale non viene toccato."""
    import psycopg

    from src.backend.clients.postgres_client import conninfo
    with psycopg.connect(conninfo()) as c:
        c.execute("DROP SCHEMA IF EXISTS test_registry CASCADE")
        c.execute("CREATE SCHEMA test_registry")
        c.execute("SET search_path TO test_registry")
        c.commit()  # senza commit il rollback di teardown annullerebbe lo schema
        yield c
        c.rollback()
        c.execute("DROP SCHEMA IF EXISTS test_registry CASCADE")
        c.commit()


@needs_pg
class TestRegistro:
    def _setup(self, conn):
        from src.registry.registry import init_schema, register_document
        init_schema(conn)
        return register_document(
            conn, slug="mamoli_catalogo", category="original_dataset",
            roles=[Role.TECNICO], doc_stem="mamoli_catalogo_2021", langs=["it", "en"])

    def test_init_schema_idempotente(self, conn):
        from src.registry.registry import init_schema
        init_schema(conn)
        init_schema(conn)  # seconda esecuzione: nessun errore

    def test_register_e_get_roundtrip(self, conn):
        from src.registry.registry import get_current_version
        res = self._setup(conn)
        assert res.created is True
        cur = get_current_version(conn, "mamoli_catalogo")
        assert cur is not None
        assert cur.doc_stem == "mamoli_catalogo_2021"
        assert cur.version_no == 1
        assert cur.version_status == "current"

    def test_register_idempotente(self, conn):
        from src.registry.registry import register_document
        self._setup(conn)
        res2 = register_document(
            conn, slug="mamoli_catalogo", category="original_dataset",
            roles=[Role.TECNICO], doc_stem="mamoli_catalogo_2021", langs=["it", "en"])
        assert res2.created is False

    def test_get_version_by_stem(self, conn):
        from src.registry.registry import get_version_by_stem
        self._setup(conn)
        v = get_version_by_stem(conn, "mamoli_catalogo_2021")
        assert v is not None and v.version_status == "current"
        assert get_version_by_stem(conn, "inesistente") is None

    def test_allowed_roles(self, conn):
        from src.registry.registry import allowed_roles
        self._setup(conn)
        assert allowed_roles(conn, "mamoli_catalogo_2021") == [Role.TECNICO]

    def test_una_sola_current_per_documento(self, conn):
        import psycopg
        res = self._setup(conn)
        with pytest.raises(psycopg.errors.UniqueViolation):
            conn.execute(
                "INSERT INTO document_versions "
                "(document_id, doc_stem, version_no, version_status) "
                "VALUES (%s, 'mamoli_catalogo_2023', 2, 'current')",
                (res.document_id,))
        conn.rollback()

    def test_mark_superseded(self, conn):
        from src.registry.registry import (
            get_current_version,
            get_version_by_stem,
            mark_superseded,
        )
        self._setup(conn)
        mark_superseded(conn, slug="mamoli_catalogo",
                        new_doc_stem="mamoli_catalogo_2023", langs=["it", "en"])
        cur = get_current_version(conn, "mamoli_catalogo")
        assert cur.doc_stem == "mamoli_catalogo_2023"
        assert cur.version_no == 2
        old = get_version_by_stem(conn, "mamoli_catalogo_2021")
        assert old.version_status == "superseded"
        assert old.superseded_at is not None

    def test_list_documents(self, conn):
        from src.registry.registry import list_documents
        self._setup(conn)
        docs = list_documents(conn)
        assert [d.slug for d in docs] == ["mamoli_catalogo"]

    def test_export_backfill_copre_current_e_superseded(self, conn):
        from src.registry.registry import export_backfill, mark_superseded
        self._setup(conn)
        mark_superseded(conn, slug="mamoli_catalogo",
                        new_doc_stem="mamoli_catalogo_2023", langs=["it", "en"])
        exp = export_backfill(conn)
        assert set(exp) == {"mamoli_catalogo_2021", "mamoli_catalogo_2023"}
        old, new = exp["mamoli_catalogo_2021"], exp["mamoli_catalogo_2023"]
        assert old.version_status == "superseded" and old.doc_version == 1
        assert new.version_status == "current" and new.doc_version == 2
        # i ruoli sono del documento LOGICO: identici su tutte le versioni
        assert old.ruoli == new.ruoli == [Role.TECNICO]


# ------------------------------------------------- payload di backfill (P4.2)

class TestBackfillPayload:
    def test_serializzazione_additiva(self):
        from src.registry.registry import BackfillRecord, backfill_payload
        rec = BackfillRecord(version_status="current", doc_version=1,
                             ruoli=[Role.COMMERCIALE, Role.TECNICO])
        p = backfill_payload(rec)
        assert p == {"version_status": "current", "doc_version": 1,
                     "ruoli_consentiti": ["commerciale", "tecnico"]}
        assert all(type(r) is str for r in p["ruoli_consentiti"])  # JSON-safe

    def test_ruoli_vuoti_rifiutati(self):
        # fail-closed: un doc senza ruoli renderebbe il filtro RBAC un buco
        from src.registry.registry import BackfillRecord, backfill_payload
        with pytest.raises(ValueError):
            backfill_payload(BackfillRecord("current", 1, []))

    def test_status_non_valido_rifiutato(self):
        from src.registry.registry import BackfillRecord, backfill_payload
        with pytest.raises(ValueError):
            backfill_payload(BackfillRecord("attivo", 1, [Role.TECNICO]))
