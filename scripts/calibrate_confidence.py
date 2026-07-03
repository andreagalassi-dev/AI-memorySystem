"""P3.2 §7 — Calibrazione di tau_hi/tau_lo e misura del "non lo so" per lingua.

Rigenera i MARGINI relativi per-scorer interrogando Qdrant (results_v1.json ha
solo i top-1 assoluti, non i top-2):
  - code   -> BM25 (routed sparse), punteggi gia' sugli hit;
  - natural/mixed -> reranker BGE sigmoidato (fix d'integrazione spec §3:
    rerank.py scarta i logit -> qui li ricalcoliamo e applichiamo sigmoid).
Usa la compute_margin TESTATA (src.retrieval.confidence). Poi:
  - fitta tau_hi (precisione ALTA alta) e tau_lo (recall del silenzio) sul
    canale reranker, con l'asimmetria della regola d'oro;
  - applica la matrice completa e misura silenzio-giusto vs pigrizia, per lingua.

Categorie (per query positiva/negativa):
  NEG      = query negativa (risposta corretta = "non lo so");
  POS_HIT1 = gold al rank 1 (rispondere e' corretto);
  POS_MISS = gold non al rank 1 / non recuperato (il top-1 e' un doc sbagliato
             -> il silenzio e' corretto).
Silenzio giusto = BASSA su NEG u POS_MISS. Pigrizia = BASSA su POS_HIT1.

Uso: EVAL_SET=data/eval/eval_set_v1.jsonl uv run scripts/calibrate_confidence.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.retrieval.confidence import (  # noqa: E402
    Signals,
    Thresholds,
    compute_margin,
    confidence_level,
)
from src.retrieval.rerank import RERANK_TOPK, _reranker  # noqa: E402
from src.retrieval.router import classify_query  # noqa: E402
from src.retrieval.search import search  # noqa: E402

EVAL_SET = Path(os.environ.get("EVAL_SET", ROOT / "data" / "eval" / "eval_set_v1.jsonl"))
OUT = ROOT / "output" / "eval" / "confidence_calibration_v1.json"
LIMIT = 10


def crit_match(payload: dict, crit: dict) -> bool:
    if "code" in crit:
        return payload.get("code") == crit["code"]
    return payload.get("doc") == crit["doc"] and payload.get("pos") == crit["pos"]


def first_relevant_rank(hits, expected) -> int | None:
    for rank, h in enumerate(hits, start=1):
        if any(crit_match(h.payload, c) for c in expected):
            return rank
    return None


def reranker_probs(query: str, hits: list) -> list[tuple[float, object]]:
    """(probabilita' sigmoid, hit) ordinati desc. Ricalcola i logit e li tiene
    (rerank.py li scarta)."""
    import torch

    if not hits:
        return []
    tok, model, device = _reranker()
    pairs = [(query, h.payload.get("text") or h.payload.get("desc") or "") for h in hits]
    batch = tok(pairs, padding=True, truncation=True, max_length=512,
                return_tensors="pt").to(device)
    with torch.no_grad():
        logits = model(**batch).logits.squeeze(-1).float()
        probs = torch.sigmoid(logits).cpu().tolist()
    if not isinstance(probs, list):
        probs = [probs]
    return sorted(zip(probs, hits), key=lambda t: -t[0])


def collect() -> list[dict]:
    """Una riga per query: scorer, margine, categoria, evidenza, lingua, tipo."""
    client = get_qdrant_client()
    pairs = [json.loads(line) for line in EVAL_SET.open(encoding="utf-8")]
    rows: list[dict] = []
    for p in pairs:
        route = classify_query(p["query"])
        is_neg = p["query_type"] == "negative"
        if route.query_type == "code":
            hits = search(client, p["query"], "routed", limit=LIMIT)
            scores = [h.score for h in hits]
            ordered = hits
            scorer = "bm25"
            has_ev = bool(hits) and hits[0].score > 0
        else:
            base = search(client, p["query"], "routed", limit=RERANK_TOPK)
            pr = reranker_probs(p["query"], base)
            scores = [s for s, _ in pr]
            ordered = [h for _, h in pr]
            scorer = "reranker"
            has_ev = bool(base)
        margin = compute_margin(scores)
        top = ordered[0].payload if ordered else {}
        unreliable = (top.get("status") == "degraded"
                      or "ocr_source" in (top.get("warnings") or []))
        rank = None if is_neg else first_relevant_rank(ordered, p["expected"])
        cat = "NEG" if is_neg else ("POS_HIT1" if rank == 1 else "POS_MISS")
        rows.append({"qa_id": p["qa_id"], "lang": p["lang"],
                     "query_type": p["query_type"], "scorer": scorer,
                     "margin": round(margin, 4), "has_evidence": has_ev,
                     "unreliable": unreliable, "gold_rank": rank, "cat": cat})
    return rows


def fit_tau(rows: list[dict]) -> Thresholds:
    """Fitta sul canale RERANKER (il code e' gia' deterministico via BM25=0).

    tau_hi = appena sopra il MASSIMO margine POS_MISS osservato: ALTA e' negata
      a tutti i casi 'confident-wrong' visti (asimmetria regola d'oro: mai un
      falso-ALTA su un doc sbagliato). Cap a 0.99.
    tau_lo = punto di Youden (max sensibilita'+specificita'-1) che separa il
      silenzio-giusto (margine < t) dai POS_HIT1 (margine >= t): il taglio
      singolo statisticamente ottimo, senza vincoli arbitrari."""
    rr = [r for r in rows if r["scorer"] == "reranker" and r["has_evidence"]]
    hit1 = [r["margin"] for r in rr if r["cat"] == "POS_HIT1"]
    silence = [r["margin"] for r in rr if r["cat"] in ("NEG", "POS_MISS")]
    posmiss = [r["margin"] for r in rr if r["cat"] == "POS_MISS"]

    tau_hi = round(min(0.99, (max(posmiss) if posmiss else 0.9) + 0.011), 2)

    # tau_lo vincolato: massimo taglio con pigrizia (POS_HIT1 sotto soglia) <=0.10.
    # Il margine reranker non separa -> non si insegue il silenzio a scapito
    # della ritenzione; i confident-wrong li zittira' P3.3 (accordo testo-visione).
    n_hit1 = len(hit1) or 1
    cand = sorted({m for m in silence + hit1} | {0.0})
    tau_lo = 0.0
    for t in cand:
        if t >= tau_hi:
            break
        if sum(1 for m in hit1 if m < t) / n_hit1 <= 0.10:
            tau_lo = t
    return Thresholds(tau_hi=tau_hi, tau_lo=round(tau_lo, 4))


def sweep_tau_lo(rows: list[dict], tau_hi: float) -> list[dict]:
    """Curva trasparente τ_lo -> (recall silenzio, pigrizia) sul canale reranker."""
    rr = [r for r in rows if r["scorer"] == "reranker" and r["has_evidence"]]
    hit1 = [r["margin"] for r in rr if r["cat"] == "POS_HIT1"]
    sil = [r["margin"] for r in rr if r["cat"] in ("NEG", "POS_MISS")]
    out = []
    for t in (0.0, 0.05, 0.1, 0.2, 0.3, 0.5):
        out.append({
            "tau_lo": t,
            "silence_recall": round(sum(1 for m in sil if m < t) / len(sil), 3) if sil else None,
            "pigrizia_rate": round(sum(1 for m in hit1 if m < t) / len(hit1), 3) if hit1 else None,
        })
    return out


def measure(rows: list[dict], tau: Thresholds) -> dict:
    """Applica la matrice completa e misura silenzio-giusto vs pigrizia."""
    def level_of(r):
        s = Signals(gate="PASS", has_evidence=r["has_evidence"],
                    margin=r["margin"], unreliable=r["unreliable"], agreement=None)
        return confidence_level(s, tau).level.value

    def bucket(subset):
        silence_set = [r for r in subset if r["cat"] in ("NEG", "POS_MISS")]
        hit1_set = [r for r in subset if r["cat"] == "POS_HIT1"]
        good_sil = sum(1 for r in silence_set if level_of(r) == "BASSA")
        pigrizia = sum(1 for r in hit1_set if level_of(r) == "BASSA")
        false_ans = sum(1 for r in silence_set if level_of(r) != "BASSA")
        return {
            "n": len(subset),
            "n_should_silence": len(silence_set), "n_answerable_hit1": len(hit1_set),
            "silenzio_giusto": good_sil,
            "silence_recall": round(good_sil / len(silence_set), 3) if silence_set else None,
            "pigrizia": pigrizia,
            "pigrizia_rate": round(pigrizia / len(hit1_set), 3) if hit1_set else None,
            "risposte_su_da_tacere": false_ans,
            "livelli": dict(sorted(defaultdict(int, {
                lv: sum(1 for r in subset if level_of(r) == lv)
                for lv in ("ALTA", "MEDIA", "BASSA")}).items())),
        }

    out = {"overall": bucket(rows)}
    by_lang = defaultdict(list)
    for r in rows:
        by_lang[r["lang"]].append(r)
    out["per_lang"] = {lang: bucket(rs) for lang, rs in sorted(by_lang.items())}
    by_scorer = defaultdict(list)
    for r in rows:
        by_scorer[r["scorer"]].append(r)
    out["per_scorer"] = {sc: bucket(rs) for sc, rs in sorted(by_scorer.items())}
    return out


def main() -> int:
    print(f"[calib] eval set: {EVAL_SET.name}")
    rows = collect()
    print(f"[calib] query: {len(rows)} "
          f"(code={sum(r['scorer']=='bm25' for r in rows)}, "
          f"reranker={sum(r['scorer']=='reranker' for r in rows)})")
    tau = fit_tau(rows)
    print(f"[calib] FIT: tau_hi={tau.tau_hi}  tau_lo={tau.tau_lo}")
    sweep = sweep_tau_lo(rows, tau.tau_hi)
    print("[calib] sweep reranker (tau_lo -> silence_recall / pigrizia):")
    for s in sweep:
        print(f"    tau_lo={s['tau_lo']:.2f}  sil_recall={s['silence_recall']}  "
              f"pigrizia={s['pigrizia_rate']}")
    report = measure(rows, tau)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({
        "date": str(date.today()), "eval_set": EVAL_SET.name,
        "tau": {"tau_hi": tau.tau_hi, "tau_lo": tau.tau_lo},
        "sweep_reranker": sweep, "measure": report, "rows": rows,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    ov = report["overall"]
    print(f"[calib] OVERALL: silenzio_giusto={ov['silenzio_giusto']}/"
          f"{ov['n_should_silence']} (recall {ov['silence_recall']}) · "
          f"pigrizia={ov['pigrizia']}/{ov['n_answerable_hit1']} "
          f"(rate {ov['pigrizia_rate']}) · livelli={ov['livelli']}")
    for lang, b in report["per_lang"].items():
        print(f"  {lang}: sil_giusto={b['silenzio_giusto']}/{b['n_should_silence']} "
              f"(rec {b['silence_recall']}) pigrizia={b['pigrizia']}/"
              f"{b['n_answerable_hit1']} (rate {b['pigrizia_rate']}) {b['livelli']}")
    print(f"[calib] scritto: {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
