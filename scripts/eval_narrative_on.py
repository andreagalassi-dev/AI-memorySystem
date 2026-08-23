"""Esegue `scripts/eval_narrative.py` contro una collezione Qdrant DIVERSA da
quella di produzione, senza modificare ne' il metro ne' i default.

Perche' un wrapper e non un flag: `src/retrieval/search.py` lega `COLLECTION` a
import-time. Aggiungere un parametro `collection` a tutta la catena
(search -> apply_hop2 -> search_unified -> deps.retrieve -> pipeline) vorrebbe
dire toccare la produzione per un esperimento. Qui si ri-lega il solo nome
globale del modulo di retrieval PRIMA di far partire il metro: le funzioni lo
leggono a runtime, quindi tutte le query del run vanno sulla collezione scelta
e nient'altro cambia. Il default resta `aike_chunks`: senza `--collection`
questo wrapper e' identico a lanciare il metro a mano.

Uso:
  .venv/bin/python scripts/eval_narrative_on.py --collection aike_chunks_v2 \\
      -- --mode retrieval --tag v2
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

    spec = importlib.util.spec_from_file_location(
        "eval_narrative_mod", ROOT / "scripts" / "eval_narrative.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)

    if args.collection:
        import src.retrieval.bom as bom
        import src.retrieval.search as search
        for m in (search, bom):
            m.COLLECTION = args.collection
        # prova che la ri-legatura ha davvero preso: un A/B che gira per sbaglio
        # sulla produzione darebbe "nessuna differenza" e sembrerebbe un
        # risultato, invece di essere un errore di misura.
        assert search.COLLECTION == args.collection
        from src.backend.clients.qdrant_client import get_qdrant_client
        n = get_qdrant_client().count(args.collection, exact=True).count
        print(f"### COLLEZIONE SOTTO MISURA: {args.collection} ({n} punti)\n")

    sys.argv = ["eval_narrative.py", *resto]
    return modulo.main()


if __name__ == "__main__":
    raise SystemExit(main())
