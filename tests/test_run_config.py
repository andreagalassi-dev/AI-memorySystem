"""La misura deve DICHIARARE la configurazione sotto cui e' stata presa.

Il difetto che questi test presidiano non e' "manca una load_dotenv": e' che due
run presi sotto configurazioni diverse (reranker visivo on/off -> pavimento
anti-confident-wrong presente/assente) risultavano indistinguibili sia a video
sia nell'audit JSON.
"""

from __future__ import annotations

import json

import pytest

from src.common.run_config import (
    effective_config,
    format_config_banner,
    load_runtime_env,
    visual_rerank_decision,
)


@pytest.fixture(autouse=True)
def _clean_rerank_env(monkeypatch):
    monkeypatch.delenv("AIKE_VISUAL_RERANK", raising=False)
    monkeypatch.delenv("AIKE_RERANK_MIN_GB", raising=False)
    yield


def test_env_esplicito_on_e_dichiarato_come_esplicito(monkeypatch):
    monkeypatch.setenv("AIKE_VISUAL_RERANK", "1")
    enabled, reason = visual_rerank_decision()
    assert enabled is True
    assert "esplicit" in reason.lower()
    assert "AIKE_VISUAL_RERANK" in reason


def test_env_esplicito_off_e_dichiarato_come_esplicito(monkeypatch):
    monkeypatch.setenv("AIKE_VISUAL_RERANK", "0")
    enabled, reason = visual_rerank_decision()
    assert enabled is False
    assert "esplicit" in reason.lower()


def test_modalita_auto_dichiara_la_ram_e_la_soglia(monkeypatch):
    monkeypatch.setenv("AIKE_VISUAL_RERANK", "auto")
    monkeypatch.setenv("AIKE_RERANK_MIN_GB", "99999")
    enabled, reason = visual_rerank_decision()
    assert enabled is False
    assert "auto" in reason.lower()
    assert "RAM" in reason
    assert "99999" in reason


def test_il_pavimento_segue_il_reranker(monkeypatch):
    from src.graph.tools.visual_tool import VISUAL_SCORE_FLOOR

    monkeypatch.setenv("AIKE_VISUAL_RERANK", "1")
    assert effective_config()["visual_score_floor"] == VISUAL_SCORE_FLOOR

    monkeypatch.setenv("AIKE_VISUAL_RERANK", "0")
    assert effective_config()["visual_score_floor"] is None


def test_config_e_serializzabile_e_ha_i_campi_obbligatori(monkeypatch):
    monkeypatch.setenv("AIKE_VISUAL_RERANK", "1")
    cfg = effective_config()
    # deve poter finire in un audit JSON senza encoder custom
    round_trip = json.loads(json.dumps(cfg))
    for key in (
        "visual_rerank",
        "visual_score_floor",
        "llm",
        "qdrant",
        "postgres",
        "env_file",
    ):
        assert key in round_trip, key
    assert round_trip["visual_rerank"]["enabled"] is True
    assert round_trip["llm"]["brain_model"]
    assert round_trip["llm"]["ollama_host"]
    assert round_trip["qdrant"]["collections"]["text"] == "aike_chunks"
    assert round_trip["qdrant"]["collections"]["visual"] == "aike_pages_visual"


def test_floor_osservato_dalle_deps_vince_sulla_previsione(monkeypatch):
    """Se il reranker esplode in init, l'env dice ON ma il floor reale e' None:
    l'audit deve riportare cio' che e' successo, non cio' che era previsto."""
    monkeypatch.setenv("AIKE_VISUAL_RERANK", "1")

    class FakeDeps:
        visual_score_floor = None
        visual_search = None

    cfg = effective_config(deps=FakeDeps())
    assert cfg["visual_score_floor"] is None
    assert cfg["visual_score_floor_source"] == "osservato"
    assert cfg["visual_search_available"] is False


def test_load_runtime_env_non_sovrascrive_le_variabili_gia_presenti(monkeypatch):
    """Stessa precedenza di chainlit_app.py: os.environ vince sul .env."""
    import os

    monkeypatch.setenv("AIKE_VISUAL_RERANK", "0")
    load_runtime_env()
    assert os.environ["AIKE_VISUAL_RERANK"] == "0"


def test_load_runtime_env_trova_il_dotenv_del_repo(monkeypatch):
    monkeypatch.delenv("AIKE_VISUAL_RERANK", raising=False)
    path = load_runtime_env()
    assert path is not None and path.endswith(".env")


def test_banner_dichiara_reranker_floor_e_modello(monkeypatch):
    monkeypatch.setenv("AIKE_VISUAL_RERANK", "1")
    banner = format_config_banner(effective_config())
    assert "CONFIGURAZIONE" in banner.upper()
    assert "reranker visivo" in banner
    assert "visual_score_floor" in banner
    assert "ollama" in banner.lower()
