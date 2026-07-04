"""Debito §1 — Test dei Profili di Ingestion su PostgreSQL.

Vincolo sotto test: Postgres e' la fonte di verita' dei profili; il round-trip
seed JSON -> tabelle -> load_profiles deve essere EQUIVALENTE (stesso dict
canonico), l'upsert idempotente, le regex validate fail-fast prima di scrivere.
Integrazione su schema temporaneo (fixture `conn`), pattern test_registry.py.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.registry.ingestion_profiles import (
    canonical_profile,
    init_schema,
    load_profiles,
    load_seed_config,
    set_doc_map,
    upsert_profile,
    validate_profile,
)
from tests.conftest import needs_pg

SEED_PATH = Path(__file__).resolve().parents[1] / "config" / "brands.json"


def seed_all(conn, cfg: dict) -> None:
    for name, profile in cfg["profiles"].items():
        upsert_profile(conn, name, profile)
    set_doc_map(conn, cfg["doc_map"])


# ------------------------------------------------------- validazione (no DB)

class TestValidazione:
    def test_regex_invalida_rifiutata(self):
        with pytest.raises(ValueError, match="regex invalida"):
            validate_profile("rotto", {"short_pattern": "[invalid"})

    def test_regex_invalida_in_famiglia_rifiutata(self):
        fam = {"name": "f", "extract_pattern": "(", "token_pattern": "^A$"}
        with pytest.raises(ValueError, match="famiglia 'f'"):
            validate_profile("rotto", {"long_families": [fam]})

    def test_seed_reale_valido(self):
        cfg = load_seed_config(SEED_PATH)
        assert "mamoli" in cfg["profiles"]
        assert "default" in cfg["profiles"]
        assert cfg["doc_map"]["MAMOLI_*"] == "mamoli"

    def test_canonical_riempe_default(self):
        p = canonical_profile({"long_families": [
            {"name": "f", "extract_pattern": "A", "token_pattern": "^A$"}]})
        assert p["passthrough"] is False
        assert p["long_families"][0]["rejoin"] is False
        assert p["short_pattern"] is None


# ------------------------------------------------------- round-trip su DB

@needs_pg
class TestRoundTrip:
    def test_seed_load_equivalente_al_json(self, conn):
        init_schema(conn)
        cfg = load_seed_config(SEED_PATH)
        seed_all(conn, cfg)
        assert load_profiles(conn) == cfg

    def test_upsert_idempotente(self, conn):
        init_schema(conn)
        cfg = load_seed_config(SEED_PATH)
        seed_all(conn, cfg)
        prima = load_profiles(conn)
        seed_all(conn, cfg)  # seconda esecuzione: stato identico
        assert load_profiles(conn) == prima

    def test_update_profilo_esistente(self, conn):
        init_schema(conn)
        cfg = load_seed_config(SEED_PATH)
        seed_all(conn, cfg)
        nuovo = dict(cfg["profiles"]["mamoli"], short_pattern=r"^[0-9]{4}$")
        upsert_profile(conn, "mamoli", nuovo)
        assert load_profiles(conn)["profiles"]["mamoli"]["short_pattern"] == r"^[0-9]{4}$"

    def test_famiglia_rimossa_se_assente(self, conn):
        init_schema(conn)
        cfg = load_seed_config(SEED_PATH)
        seed_all(conn, cfg)
        senza_famiglie = dict(cfg["profiles"]["mamoli"], long_families=[])
        upsert_profile(conn, "mamoli", senza_famiglie)
        assert load_profiles(conn)["profiles"]["mamoli"]["long_families"] == []

    def test_ordine_famiglie_per_position(self, conn):
        init_schema(conn)
        fams = [
            {"name": "seconda", "extract_pattern": "B\\d{3}", "token_pattern": "^B\\d{3}$"},
            {"name": "prima", "extract_pattern": "A\\d{3}", "token_pattern": "^A\\d{3}$"},
        ]
        upsert_profile(conn, "multi", {"long_families": fams})
        loaded = load_profiles(conn)["profiles"]["multi"]["long_families"]
        assert [f["name"] for f in loaded] == ["seconda", "prima"]

    def test_doc_map_glob_stantio_rimosso(self, conn):
        init_schema(conn)
        cfg = load_seed_config(SEED_PATH)
        seed_all(conn, cfg)
        ridotta = {"MAMOLI_*": "mamoli"}
        set_doc_map(conn, ridotta)
        assert load_profiles(conn)["doc_map"] == ridotta

    def test_doc_map_profilo_inesistente_rifiutato(self, conn):
        init_schema(conn)
        with pytest.raises(ValueError, match="inesistente"):
            set_doc_map(conn, {"Bosch_*": "bosch"})

    def test_regex_invalida_non_scrive_nulla(self, conn):
        init_schema(conn)
        with pytest.raises(ValueError, match="regex invalida"):
            upsert_profile(conn, "rotto", {"short_pattern": "[invalid"})
        assert load_profiles(conn)["profiles"] == {}

    def test_profilo_disattivato_sparisce_dal_load(self, conn):
        init_schema(conn)
        cfg = load_seed_config(SEED_PATH)
        seed_all(conn, cfg)
        conn.execute("UPDATE ingestion_profiles SET is_active = false "
                     "WHERE name = 'electrolux'")
        conn.commit()
        loaded = load_profiles(conn)
        assert "electrolux" not in loaded["profiles"]
        assert "Electrolux_*" not in loaded["doc_map"]
