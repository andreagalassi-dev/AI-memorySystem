"""I bersagli dei truth set visivi esistono davvero nell'indice? (SOLO Qdrant)

Un bersaglio `(doc, page)` assente da `aike_pages_visual` produce un recall 0 che
sembra un difetto del MOTORE ed e' un difetto del METRO. E' gia' successo in
questo progetto, e costa giornate di caccia al bug sbagliato.

Questo controllo e' deliberatamente separato da `audit_visual_graph_eval.py`:
quello carica embedder e reranker (serve RAM libera), questo interroga solo
Qdrant in lettura e gira sempre, anche a macchina piena. Verificare il metro
deve costare zero, altrimenti non lo si verifica.

    env -u PYTHONPATH PYTHONPATH=. .venv/bin/python scripts/check_visual_truth_targets.py

Exit code 1 se manca almeno un bersaglio.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.common.run_config import load_runtime_env  # noqa: E402

load_runtime_env()

from scripts.audit_visual_graph_eval import indexed_pages, load_pairs  # noqa: E402

DEFAULT_EVAL = [ROOT / "data" / "eval" / "eval_visual_v0.jsonl",
                ROOT / "data" / "eval" / "eval_visual_clean.jsonl"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--eval", nargs="+", default=[str(p) for p in DEFAULT_EVAL])
    args = ap.parse_args(argv)

    from src.backend.clients.qdrant_client import get_qdrant_client
    from src.retrieval.pixelrag.index import DEFAULT_VISUAL_COLLECTION

    indexed = indexed_pages(get_qdrant_client(), DEFAULT_VISUAL_COLLECTION)
    docs = {d for d, _ in indexed}
    print(f"[{DEFAULT_VISUAL_COLLECTION}] pagine={len(indexed)} documenti={len(docs)}")

    mancanti = 0
    for source, pair in load_pairs(args.eval):
        attesi = [(c["doc"], p) for c in pair["expected"] for p in c["pages"]]
        assenti = [e for e in attesi if e not in indexed]
        doc_assenti = sorted({d for d, _ in attesi if d not in docs})
        if assenti:
            mancanti += 1
            print(f"MISS {source}/{pair['qa_id']}: {len(assenti)}/{len(attesi)} "
                  f"bersagli ASSENTI {assenti}"
                  + (f" (documento mai indicizzato: {doc_assenti})" if doc_assenti else ""))
        else:
            print(f"OK   {source}/{pair['qa_id']}: {len(attesi)}/{len(attesi)} presenti")

    print(f"\nquery con bersagli mancanti: {mancanti}")
    return 1 if mancanti else 0


if __name__ == "__main__":
    sys.exit(main())
