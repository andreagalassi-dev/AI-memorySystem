"""P2.6 — Test del registro profili brand (debito §1: fonte Postgres).

Richiedono il Postgres del compose SEEDATO (scripts/seed_ingestion_profiles.py):
dal debito §1 i profili vivono a DB, fail-closed senza fallback JSON.
"""

from __future__ import annotations

import pytest

from src.ingestion.brands import get_profile, normalize_cell, reload_profiles, resolve
from src.retrieval.router import classify_query
from tests.conftest import needs_pg

pytestmark = needs_pg


class TestResolve:
    def test_doc_map(self):
        assert resolve("Mamoli_Catalogo_Ricambi_2021_IT_EN")["_name"] == "mamoli"
        assert resolve("ELECTROLUX_ES")["_name"] == "electrolux"
        assert resolve("DeliTools_SpareParts_List")["_name"] == "delitools"

    def test_brand_ignoto_va_al_default(self):
        assert resolve("branson_4520R_parts")["_name"] == "default"
        assert resolve("kohler_industrial_tp7120")["_name"] == "default"


class TestProfiloMamoli:
    def test_rejoin_identico_a_p1(self):
        codes, logs = normalize_cell("V0 91568 00007", get_profile("mamoli"))
        assert codes == ["V09156800007"]
        assert logs[0]["type"] == "safe_rejoin"
        assert logs[0]["reason"] == "famiglia V0: uniti blocchi separati da spazi/markup"


class TestProfiloElectrolux:
    def test_famiglia_0us(self):
        codes, logs = normalize_cell("0USBX4", get_profile("electrolux"))
        assert codes == ["0USBX4"] and logs[0]["type"] == "safe_exact_cleanup"

    def test_non_inventa_su_testo(self):
        codes, _ = normalize_cell("Sealing Gasket", get_profile("electrolux"))
        assert codes == []


class TestProfiloDefault:
    def test_passthrough_token_codey(self):
        codes, logs = normalize_cell("A2300AZ-R", get_profile("default"))
        assert codes == ["A2300AZ-R"]
        assert logs[0]["type"] == "default_passthrough"

    def test_nessun_rejoin_mai(self):
        # il default NON ricompone: 'V0 91568 00007' resta non-codice
        codes, logs = normalize_cell("V0 91568 00007", get_profile("default"))
        assert codes == []

    def test_testo_normale_ignorato(self):
        assert normalize_cell("Coppia maniglie", get_profile("default"))[0] == []
        assert normalize_cell("58,00", get_profile("default"))[0] == []

    def test_solo_cifre_corte_ignorate(self):
        # '2' o '5840' senza lettere: sotto soglia -> non e' un codice
        assert normalize_cell("50", get_profile("default"))[0] == []
        assert normalize_cell("340006734", get_profile("default"))[0] == ["340006734"]


class TestRouterDaConfig:
    def test_famiglie_multi_brand_tier_a(self):
        r = classify_query("cerco il ricambio 0USBX4 per il congelatore")
        assert (r.query_type, r.code_token) == ("mixed", "0USBX4")
        r = classify_query("V09156800007")
        assert r.query_type == "code"


class TestFonteDB:
    """Debito §1: profili da Postgres, fail-closed, cache invalidabile."""

    @pytest.fixture(autouse=True)
    def _cache_pulita(self):
        reload_profiles()
        yield
        reload_profiles()

    def test_fail_closed_senza_postgres(self, monkeypatch):
        import psycopg

        from src.backend.clients import postgres_client

        def _giu():
            raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(postgres_client, "get_pg_conn", _giu)
        with pytest.raises(RuntimeError, match="Fail-closed"):
            get_profile("mamoli")

    def test_profilo_nuovo_visibile_dopo_reload(self, monkeypatch):
        from src.ingestion import brands
        cfg = {
            "profiles": {
                "default": {"long_families": [], "short_pattern": None,
                            "reason_short": None, "unsafe_numeric": None,
                            "reason_unsafe": None, "passthrough": True},
                "bosch": {"long_families": [
                    {"name": "F-lunga", "extract_pattern": r"F(?:[\s*_`]*\d){9}",
                     "token_pattern": r"^F\d{9}$", "rejoin": False,
                     "reason_rejoin": "bosch: cleanup", "reason_double": "split"}],
                    "short_pattern": None, "reason_short": None,
                    "unsafe_numeric": None, "reason_unsafe": None,
                    "passthrough": False},
            },
            "doc_map": {"Bosch_*": "bosch"},
        }
        monkeypatch.setattr(brands, "_load_from_db", lambda: cfg)
        reload_profiles()
        assert resolve("Bosch_Catalogo_2026")["_name"] == "bosch"
        codes, _ = normalize_cell("F 000 026 341", get_profile("bosch"))
        assert codes == ["F000026341"]
