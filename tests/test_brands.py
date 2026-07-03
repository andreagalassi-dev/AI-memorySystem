"""P2.6 — Test del registro profili brand (config/brands.json)."""

from __future__ import annotations

from src.ingestion.brands import get_profile, normalize_cell, resolve
from src.retrieval.router import classify_query


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
