"""P3.4 — Spike del ragionatore 14B: sintesi ancorata + gate deterministici.

Su ~query eval con EVIDENZA reale (routed): chiama qwen2.5:14b e misura i gate
che RIUSANO i guardrail (difesa in profondita'):
  - no codici allucinati: guardrail.check_answer(output, evidenza) != BLOCK;
  - citazione obbligatoria: ogni (doc, pag.) citato e' nell'evidenza; >=1 se
    non e' un rifiuto;
  - obbedienza "non lo so": evidenza VUOTA -> stringa di rifiuto esatta.
Piu' latenza e output cross-lingua mostrati per giudizio umano. Osserva
l'output REALE del modello (non check statici).

Uso: uv run scripts/spike_llm.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdrant_client import models  # noqa: E402

from src.backend.clients.ollama_client import get_ollama_client  # noqa: E402
from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.backend.config import settings  # noqa: E402
from src.retrieval.guardrail import check_answer  # noqa: E402
from src.retrieval.search import search  # noqa: E402
from src.retrieval.synthesis import (  # noqa: E402
    REFUSAL,
    extract_citations,
    strip_citations,
    synthesize,
)

EVAL_SET = Path(os.environ.get("EVAL_SET", ROOT / "data" / "eval" / "eval_set_v1.jsonl"))
MODEL = os.environ.get("SPIKE_MODEL", settings.ollama_model)
OUT = ROOT / "output" / "eval" / "spike_llm_14b.json"
# Selezione curata: mix lingua + cross-lingua; le ultime 2 con evidenza VUOTA.
QIDS = ["W002", "W009", "W014", "C001", "C008", "W030", "W040"]
EMPTY_EVIDENCE = ["W002", "C001"]  # ripetute a evidenza vuota -> rifiuto atteso


def catalog_lookup(client):
    def lookup(key: str) -> bool:
        r = client.count("aike_chunks", exact=True, count_filter=models.Filter(
            must=[models.FieldCondition(key="code", match=models.MatchValue(value=key))]))
        return r.count > 0
    return lookup


def evidence_for(client, query: str, n: int = 6) -> list[dict]:
    return [h.payload for h in search(client, query, "routed", limit=n)]


def main() -> int:
    qc = get_qdrant_client()
    oc = get_ollama_client()
    lookup = catalog_lookup(qc)
    pairs = {json.loads(x)["qa_id"]: json.loads(x)
             for x in EVAL_SET.open(encoding="utf-8")}
    print(f"[spike] modello={MODEL}")

    rows = []
    cases = [(q, False) for q in QIDS] + [(q, True) for q in EMPTY_EVIDENCE]
    for qid, empty in cases:
        p = pairs[qid]
        payloads = [] if empty else evidence_for(qc, p["query"])
        t0 = time.time()
        out = synthesize(oc, p["query"], payloads, model=MODEL)
        dt = round(time.time() - t0, 2)

        cites = extract_citations(out)
        ev_dp = {(pl.get("doc"), pl.get("page")) for pl in payloads}
        is_refusal = REFUSAL in out
        gate_cite = is_refusal or (len(cites) >= 1 and all(c in ev_dp for c in cites))
        # guardrail SOLO sulle affermazioni: le citazioni (nomi-doc) non vanno
        # scansionate (contengono sotto-token code-shaped -> falsi-positivi).
        gate_nocode = check_answer(strip_citations(out), payloads, lookup).gate != "BLOCK"
        gate_refusal = (is_refusal if empty else True)
        rows.append({
            "qa_id": qid, "lang": p["lang"], "empty_evidence": empty,
            "latency_s": dt, "is_refusal": is_refusal, "n_cites": len(cites),
            "gate_no_codici_allucinati": gate_nocode,
            "gate_citazione": gate_cite, "gate_rifiuto": gate_refusal,
            "query": p["query"], "output": out,
        })
        tag = "VUOTA" if empty else p["lang"]
        print(f"  {qid:5s}[{tag:5s}] {dt:5.1f}s  nocode={gate_nocode} "
              f"cite={gate_cite} refuse={gate_refusal}  cites={len(cites)}")

    real = [r for r in rows if not r["empty_evidence"]]
    empty = [r for r in rows if r["empty_evidence"]]
    summary = {
        "n_real": len(real), "n_empty": len(empty),
        "no_codici_allucinati": sum(r["gate_no_codici_allucinati"] for r in real),
        "citazione_ok": sum(r["gate_citazione"] for r in real),
        "rifiuto_ok_su_vuota": sum(r["gate_rifiuto"] for r in empty),
        "latenza_mediana_s": sorted(r["latency_s"] for r in real)[len(real) // 2],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"model": MODEL, "summary": summary, "rows": rows},
                              indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[spike] SOMMARIO: {summary}")
    print(f"[spike] scritto: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
