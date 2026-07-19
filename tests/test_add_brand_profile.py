"""Test per lo script CLI di aggiunta profilo brand (Fase C2)."""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.add_brand_profile import main  # noqa: E402


def test_cli_add_profile_success(monkeypatch, capsys):
    """Testa l'inserimento corretto di un profilo bypassando la connessione reale."""
    # Simula argomenti CLI
    test_args = [
        "add_brand_profile.py",
        "--name", "testbrand",
        "--glob", "TestBrand_*",
        "--extract", r"TEST\d{5}",
        "--token", r"^TEST\d{5}$",
        "--test-codes", "TEST12345",
        "--yes"
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    # Mocka get_pg_conn, upsert_profile, load_profiles, set_doc_map
    mock_conn = MagicMock()
    mock_conn_ctx = MagicMock()
    mock_conn_ctx.__enter__.return_value = mock_conn

    with patch("scripts.add_brand_profile.get_pg_conn", return_value=mock_conn_ctx), \
         patch("scripts.add_brand_profile.upsert_profile", return_value=999) as mock_upsert, \
         patch("scripts.add_brand_profile.load_profiles", return_value={"profiles": {}, "doc_map": {}}) as mock_load, \
         patch("scripts.add_brand_profile.set_doc_map") as mock_set:

        ret = main()
        
        assert ret == 0
        
        # Verifica chiamate
        mock_upsert.assert_called_once()
        args, kwargs = mock_upsert.call_args
        assert args[1] == "testbrand"
        assert args[2]["long_families"][0]["extract_pattern"] == r"TEST\d{5}"
        assert args[2]["long_families"][0]["token_pattern"] == r"^TEST\d{5}$"

        mock_load.assert_called_once()
        mock_set.assert_called_once()
        
        # Il doc_map deve contenere la nuova associazione
        assert mock_set.call_args[0][1] == {"TestBrand_*": "testbrand"}

    # Verifica output
    captured = capsys.readouterr()
    assert "Token canonico valido: True" in captured.out
    assert "OK: Profilo 'testbrand' (ID 999)" in captured.out


def test_cli_invalid_regex(monkeypatch, capsys):
    """Testa il fallimento fail-fast su regex malformata."""
    test_args = [
        "add_brand_profile.py",
        "--name", "badbrand",
        "--glob", "Bad_*",
        "--extract", r"BAD[",  # regex non chiusa
        "--token", r"^BAD$",
        "--yes"
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    ret = main()
    assert ret == 1
    
    captured = capsys.readouterr()
    assert "ERRORE: Regex invalida" in captured.out


def test_cli_test_codes_fail(monkeypatch, capsys):
    """Testa il fallimento se i test codes non matchano e non c'e' yes."""
    test_args = [
        "add_brand_profile.py",
        "--name", "failbrand",
        "--glob", "Fail_*",
        "--extract", r"FAIL\d{3}",
        "--token", r"^FAIL\d{3}$",
        "--test-codes", "FAIL12"  # Mancano cifre, fallira'
    ]
    monkeypatch.setattr(sys, "argv", test_args)

    # Diciamo di no alla richiesta di procedere
    monkeypatch.setattr("builtins.input", lambda _: "n")

    ret = main()
    assert ret == 1
    
    captured = capsys.readouterr()
    assert "Token canonico valido: False" in captured.out
    assert "alcuni codici di test non hanno passato la verifica" in captured.out
    assert "Operazione annullata" in captured.out
