"""Test di `scripts/clean_test_sandboxes.py` e del meccanismo di teardown a
fixture usato in `tests/test_visual_collection_param.py`.

Contesto (vedi handoff): il test live in test_visual_collection_param.py crea
uno schema Postgres `c_vis_<hash8>` e due collezioni Qdrant `chunks_<hash8>` /
`visual_<hash8>` usa-e-getta. Se il processo viene interrotto duramente
(kill -9, TaskStop, crash) i residui restano — misurati sul DB vivo: 9 schemi
`c_vis_*`, 18 collezioni `chunks_*`/`visual_*`.

Questo file NON carica modelli, NON scrive su Postgres/Qdrant (le uniche due
query reali sono liste di sola lettura, marcate `needs_pg`/`needs_qdrant`) e
NON esegue nulla marcato `@pytest.mark.live`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conftest import needs_pg, needs_qdrant

from scripts.clean_test_sandboxes import (
    PG_SCHEMA_BLACKLIST,
    QDRANT_COLLECTION_BLACKLIST,
    delete_pg_schema,
    delete_qdrant_collection,
    find_pg_sandbox_schemas,
    find_qdrant_sandbox_collections,
    is_sandbox_pg_schema,
    is_sandbox_qdrant_collection,
    main,
)


# --- 1. Il pattern/lista-nera rifiuta i nomi di produzione ---------------

@pytest.mark.parametrize("name", sorted(PG_SCHEMA_BLACKLIST) + ["public", "estrazione", "chainlit"])
def test_rejects_production_pg_schema_names(name):
    assert is_sandbox_pg_schema(name) is False


@pytest.mark.parametrize(
    "name",
    sorted(QDRANT_COLLECTION_BLACKLIST)
    + [
        "aike_chunks",
        "aike_chunks_v2",
        "aike_chunks_v2en",
        "aike_pages_visual",
        "aike_pages_visual_colqwen3",
    ],
)
def test_rejects_production_qdrant_collection_names(name):
    assert is_sandbox_qdrant_collection(name) is False


def test_rejects_typo_like_names_that_dont_fully_match_pattern():
    """Un refuso che si avvicina a un nome di produzione ma non lo E' non deve
    ne' essere accettato per il pattern (non ha la forma hex8) ne' confuso con
    la sandbox."""
    assert is_sandbox_qdrant_collection("aike_chunks_v3") is False
    assert is_sandbox_qdrant_collection("aike_chunks_v2_typo") is False
    assert is_sandbox_pg_schema("c_vis_prod") is False  # non e' hex8
    assert is_sandbox_pg_schema("c_vis_") is False


def test_accepts_well_formed_sandbox_names():
    assert is_sandbox_pg_schema("c_vis_deadbeef") is True
    assert is_sandbox_qdrant_collection("chunks_deadbeef") is True
    assert is_sandbox_qdrant_collection("visual_0123abcd") is True


def test_delete_functions_refuse_non_sandbox_names():
    """Difesa in profondita': anche le funzioni di cancellazione, non solo il
    filtro di selezione, si rifiutano di operare su un nome non-sandbox — nel
    caso (bug futuro) qualcuno le chiami direttamente bypassando i filtri."""
    with pytest.raises(AssertionError):
        delete_pg_schema(mock.MagicMock(), "public")
    with pytest.raises(AssertionError):
        delete_qdrant_collection(mock.MagicMock(), "aike_pages_visual")


# --- 2. Il dry-run (default) non cancella nulla ---------------------------

def test_dry_run_never_calls_delete(monkeypatch):
    """Con args vuoti (default = dry-run) le funzioni di cancellazione non
    devono MAI essere invocate, a prescindere da cosa trova la lista reale."""
    calls = []
    monkeypatch.setattr(
        "scripts.clean_test_sandboxes.delete_pg_schema",
        lambda *a, **k: calls.append(("pg", a, k)),
    )
    monkeypatch.setattr(
        "scripts.clean_test_sandboxes.delete_qdrant_collection",
        lambda *a, **k: calls.append(("qdrant", a, k)),
    )
    rc = main([])
    assert rc == 0
    assert calls == [], f"dry-run ha chiamato delete: {calls}"


def test_dry_run_flag_explicit_never_calls_delete(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "scripts.clean_test_sandboxes.delete_pg_schema",
        lambda *a, **k: calls.append(("pg", a, k)),
    )
    monkeypatch.setattr(
        "scripts.clean_test_sandboxes.delete_qdrant_collection",
        lambda *a, **k: calls.append(("qdrant", a, k)),
    )
    rc = main(["--dry-run"])
    assert rc == 0
    assert calls == []


def test_dry_run_wins_over_apply_when_both_passed(monkeypatch):
    """--apply --dry-run insieme: vince --dry-run (nessuna cancellazione)."""
    calls = []
    monkeypatch.setattr(
        "scripts.clean_test_sandboxes.delete_pg_schema",
        lambda *a, **k: calls.append(("pg", a, k)),
    )
    monkeypatch.setattr(
        "scripts.clean_test_sandboxes.delete_qdrant_collection",
        lambda *a, **k: calls.append(("qdrant", a, k)),
    )
    rc = main(["--apply", "--dry-run"])
    assert rc == 0
    assert calls == []


# --- 3. Riconosce i residui reali (sola lettura) --------------------------

@needs_pg
def test_finds_real_pg_sandbox_residues():
    import psycopg
    from src.backend.clients.postgres_client import conninfo

    with psycopg.connect(conninfo()) as conn:
        found = find_pg_sandbox_schemas(conn)
    # Misurato dal vivo in questa sessione: 9 schemi c_vis_* orfani.
    # Soglia morbida (>=1, non ==9): questo test non deve rompersi il giorno
    # in cui qualcuno lancia --apply e li ripulisce per davvero.
    assert len(found) >= 1, "attesi residui c_vis_* reali sul DB del compose"
    assert all(is_sandbox_pg_schema(s) for s in found)


@needs_qdrant
def test_finds_real_qdrant_sandbox_residues():
    from src.backend.clients.qdrant_client import get_qdrant_client

    qc = get_qdrant_client()
    found = find_qdrant_sandbox_collections(qc)
    # Misurato dal vivo in questa sessione: 18 collezioni chunks_*/visual_* orfane.
    assert len(found) >= 1, "attese collezioni chunks_*/visual_* reali su Qdrant"
    assert all(is_sandbox_qdrant_collection(c) for c in found)


# --- 4. Il meccanismo di teardown a fixture sopravvive a un'eccezione -----
# Prova black-box: un vero sub-processo pytest, su un modulo di test USA E
# GETTA scritto in tmp_path (nessuna risorsa reale, nessuna scrittura su
# Postgres/Qdrant), con una fixture-con-yield la cui parte di teardown scrive
# un marker file. Il corpo del test finto solleva un'eccezione PRIMA che il
# marker possa esistere per altra via. Se il marker esiste dopo il run, il
# teardown e' girato nonostante l'eccezione nel corpo — la stessa garanzia
# usata da sandbox_pg_schema/sandbox_qdrant_collections.

_FIXTURE_TEARDOWN_TEST_MODULE = '''
import pytest

def test_body_raises(request):
    marker_dir = request.config._marker_dir
    def cleanup():
        (marker_dir / "teardown_ran.marker").write_text("cleaned\\n")
    request.addfinalizer(cleanup)
    raise ValueError("boom: simula un'eccezione nel corpo del test")
'''


def test_fixture_teardown_runs_when_test_body_raises(tmp_path):
    """Sub-processo pytest reale: la fixture-teardown (via request.addfinalizer,
    la stessa primitiva dietro `yield` in una fixture) scrive il marker anche
    se il test fallisce con un'eccezione nel corpo."""
    import subprocess
    import sys

    marker_dir = tmp_path / "markers"
    marker_dir.mkdir()
    mod = tmp_path / "test_inner_raises.py"
    mod.write_text(_FIXTURE_TEARDOWN_TEST_MODULE)
    conftest = tmp_path / "conftest.py"
    conftest.write_text(
        "import pytest\n"
        "def pytest_configure(config):\n"
        f"    config._marker_dir = __import__('pathlib').Path({str(marker_dir)!r})\n"
    )

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(mod)],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
    )

    # Il test finto FALLISCE (solleva ValueError apposta): questo e' atteso.
    assert result.returncode == 1, (
        f"atteso 1 test fallito, ottenuto rc={result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "1 failed" in result.stdout, result.stdout
    # Nonostante il fallimento, il finalizer registrato PRIMA dell'eccezione
    # deve aver girato: e' esattamente la garanzia su cui contano le fixture
    # sandbox_pg_schema / sandbox_qdrant_collections.
    marker = marker_dir / "teardown_ran.marker"
    assert marker.exists(), (
        "il finalizer della fixture NON e' girato dopo l'eccezione nel corpo del test\n"
        f"stdout:\n{result.stdout}"
    )
