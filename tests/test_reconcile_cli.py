"""Fase 2.1 — Contratto del CLI di riconciliazione (DB-free).

Il ruolo e' OBBLIGATORIO ed esplicito (mai default silenzioso) e fail-closed su un
ruolo ignoto: entrambe le validazioni avvengono PRIMA di aprire connessioni, quindi
il test non tocca ne' Postgres ne' Qdrant.
"""

from __future__ import annotations

import pytest

from src.merge.reconcile import main


def test_role_obbligatorio():
    with pytest.raises(SystemExit):        # argparse: --role mancante -> exit(2)
        main([])


def test_role_ignoto_fail_closed():
    with pytest.raises(ValueError):        # parse_role: ruolo fuori enum -> solleva
        main(["--role", "banana"])
