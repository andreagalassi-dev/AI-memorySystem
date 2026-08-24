"""Gira il golden gate su una collezione testuale DIVERSA, senza toccare i default.

Stesso mestiere (e stessa disciplina) di `scripts/eval_narrative_on.py`: serve a
misurare un indice candidato PRIMA di sostituire quello di produzione. Il gate e'
l'unico metro che guarda le RISPOSTE e non solo il recupero, quindi e' il
passaggio obbligato prima di uno scambio di indice.

Uso:
  .venv/bin/python scripts/golden_gate_on.py --collection aike_chunks_v2

Senza `--collection` e' identico a lanciare `scripts/golden_gate.py` a mano.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default=None,
                    help="collezione testuale (default: quella di produzione)")
    args, resto = ap.parse_known_args()
    if resto and resto[0] == "--":
        resto = resto[1:]

    if args.collection:
        import src.retrieval.bom as bom
        import src.retrieval.search as search
        for m in (search, bom):
            m.COLLECTION = args.collection
        # La stessa prova di `eval_narrative_on`: una misura che gira per sbaglio
        # sulla produzione direbbe "nessuna differenza" e sembrerebbe un
        # risultato, mentre e' un errore di misura.
        assert search.COLLECTION == args.collection
        from src.backend.clients.qdrant_client import get_qdrant_client
        n = get_qdrant_client().count(args.collection, exact=True).count
        print(f"### COLLEZIONE SOTTO MISURA: {args.collection} ({n} punti)\n")

    # `golden_gate.py` non ha un `main()`: l'entry point e' `run_harness`, e il
    # blocco `if __name__ == "__main__"` non scatta sotto exec_module. Si carica
    # il modulo (che NON esegue nulla) e si chiama la funzione direttamente.
    spec = importlib.util.spec_from_file_location(
        "golden_gate_mod", ROOT / "scripts" / "golden_gate.py")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules["golden_gate_mod"] = modulo
    spec.loader.exec_module(modulo)

    ap2 = argparse.ArgumentParser()
    ap2.add_argument("--update-baseline", action="store_true")
    ap2.add_argument("--limit", type=int, default=None)
    opts = ap2.parse_args(resto)
    return modulo.run_harness(update_baseline=opts.update_baseline, limit=opts.limit)


if __name__ == "__main__":
    raise SystemExit(main())
