"""P2.4 — Harness di misura del retrieval: recall@k + MRR per strategia/lingua/tipo.

Fasi (fail-fast):
  1. Carica eval set (data/eval/eval_set_v0.jsonl) e TUTTI i payload indicizzati.
  2. VALIDATORE answerability: ogni criterio `expected` deve matchare >=1 chunk
     indicizzato; i codici delle query negative devono essere ASSENTI dall'indice.
     Coppia rotta -> exit 1 (un eval set marcio produce numeri falsi).
  3. Per ogni coppia positiva x strategia (dense/sparse/rrf/routed): top-10 ->
     recall@1/3/5/10 + MRR. Hit = payload che soddisfa un criterio (code esatto,
     oppure doc+pos per le righe senza codice).
  4. Negative: mai nel recall; raccolta dei top-1 score per l'analisi soglia
     "non lo so" (separata per scala di score: sparse/BM25 vs RRF vs dense).
  5. Scrive output/eval/p24_results.json + docs/p2-metrics.md (committabile).

Uso: uv run scripts/evaluate_retrieval.py
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
from src.ingestion.embedding import COLLECTION  # noqa: E402
from src.retrieval.router import classify_query  # noqa: E402
from src.retrieval.search import STRATEGIES, search  # noqa: E402

EVAL_SET = Path(os.environ.get("EVAL_SET",
                               ROOT / "data" / "eval" / "eval_set_v0.jsonl"))
_TAG = EVAL_SET.stem.replace("eval_set_", "")
OUT_JSON = ROOT / "output" / "eval" / f"results_{_TAG}.json"
OUT_MD = ROOT / "docs" / "p2-metrics.md"   # scritto solo per il set v0 (P2.4)
KS = (1, 3, 5, 10)
LIMIT = 10
RERANK = os.environ.get("RERANK") == "1"

# stopword minime multi-lingua per il conteggio dell'overlap lessicale
_STOP = set("il lo la le i gli un una di a da in con su per tra fra del della "
            "dei delle e o non che mi serve the a an of for to and or with on "
            "in is are my der die das den dem des und mit für von ein eine "
            "einer bei auf el la los las de del para con y o un una le les des "
            "du et ou pour avec da dal dallo alla ai alle nel nella "
            "che cosa quale quanti quante how many what which vom als "
            "è e' ha ho al à au aux".split())


def content_words(text: str) -> set[str]:
    """Parole-contenuto: alfabetiche, non stopword, senza cifre (i codici e le
    misure sono ammessi per contratto)."""
    import re as _re

    return {w for w in _re.findall(r"[a-zà-ÿäöüß]+", (text or "").lower())
            if w not in _STOP and len(w) > 2}


def load_pairs() -> list[dict]:
    return [json.loads(line) for line in EVAL_SET.open(encoding="utf-8")]


def load_index_payloads(client) -> list[dict]:
    payloads, offset = [], None
    while True:
        points, offset = client.scroll(COLLECTION, limit=256, offset=offset,
                                       with_payload=True, with_vectors=False)
        payloads.extend(p.payload for p in points)
        if offset is None:
            return payloads


def crit_match(payload: dict, crit: dict) -> bool:
    if "code" in crit:
        return payload.get("code") == crit["code"]
    return payload.get("doc") == crit["doc"] and payload.get("pos") == crit["pos"]


def validate(pairs: list[dict], payloads: list[dict]) -> list[str]:
    errors = []
    indexed_codes = {p.get("code") for p in payloads if p.get("code")}
    for pair in pairs:
        if pair["query_type"] == "negative":
            route = classify_query(pair["query"])
            if route.code_token and route.code_token in indexed_codes:
                errors.append(f"{pair['qa_id']}: codice negativo "
                              f"{route.code_token} ESISTE nell'indice")
            if pair["expected"]:
                errors.append(f"{pair['qa_id']}: negative con expected non vuoto")
            continue
        if not pair["expected"]:
            errors.append(f"{pair['qa_id']}: positiva senza criteri expected")
        for crit in pair["expected"]:
            if not any(crit_match(p, crit) for p in payloads):
                errors.append(f"{pair['qa_id']}: criterio {crit} non matcha "
                              f"nessun chunk indicizzato")
    return errors


def first_relevant_rank(hits, expected) -> int | None:
    for rank, h in enumerate(hits, start=1):
        if any(crit_match(h.payload, c) for c in expected):
            return rank
    return None


def aggregate(rows: list[dict], key: str) -> dict:
    """rows: [{strategy, lang, query_type, rank}] -> metriche per (strategy, key)."""
    groups: dict = defaultdict(list)
    for r in rows:
        groups[(r["strategy"], r.get(key, "-"))].append(r["rank"])
    out: dict = defaultdict(dict)
    for (strategy, k), ranks in sorted(groups.items()):
        m = {f"recall@{kk}": round(sum(1 for r in ranks if r and r <= kk)
                                   / len(ranks), 3) for kk in KS}
        m["MRR"] = round(sum(1 / r for r in ranks if r) / len(ranks), 3)
        m["n"] = len(ranks)
        out[strategy][k] = m
    return out


def md_table(per_key: dict, title: str) -> list[str]:
    lines = [f"### {title}", "",
             "| strategia | gruppo | n | R@1 | R@3 | R@5 | R@10 | MRR |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    for strategy, groups in per_key.items():
        for g, m in groups.items():
            lines.append(f"| {strategy} | {g} | {m['n']} | {m['recall@1']} | "
                         f"{m['recall@3']} | {m['recall@5']} | {m['recall@10']} | "
                         f"{m['MRR']} |")
    lines.append("")
    return lines


def main() -> int:
    pairs = load_pairs()
    client = get_qdrant_client()
    payloads = load_index_payloads(client)
    print(f"[eval] coppie={len(pairs)} punti indicizzati={len(payloads)}")

    errors = validate(pairs, payloads)
    if errors:
        print("[eval] VALIDATORE ANSWERABILITY FALLITO:")
        for e in errors:
            print("  -", e)
        return 1
    print(f"[eval] validatore answerability: OK "
          f"({sum(1 for p in pairs if p['query_type'] != 'negative')} positive, "
          f"{sum(1 for p in pairs if p['query_type'] == 'negative')} negative)")

    # ---- overlap lessicale query<->desc target (prova di avversarialita') ----
    overlaps = []
    for pair in pairs:
        if pair["query_type"] in ("negative", "code"):
            continue
        target_descs = [p.get("desc") or "" for p in payloads
                        if any(crit_match(p, c) for c in pair["expected"])]
        if not target_descs:
            continue
        qw = content_words(pair["query"])
        ov = min(len(qw & content_words(d)) for d in target_descs)
        overlaps.append({"qa_id": pair["qa_id"], "overlap": ov})
    ovs = sorted(o["overlap"] for o in overlaps)
    overlap_stats = {"n": len(ovs),
                     "mediana": ovs[len(ovs) // 2] if ovs else None,
                     "max": max(ovs) if ovs else None,
                     "sopra_1": [o["qa_id"] for o in overlaps if o["overlap"] > 1]}
    print(f"[eval] overlap lessicale query<->target: mediana="
          f"{overlap_stats['mediana']} max={overlap_stats['max']} "
          f"sopra_1={overlap_stats['sopra_1']}")

    import time as _time

    strategies = list(STRATEGIES) + (["routed_rr"] if RERANK else [])
    latency: dict = defaultdict(list)
    rows: list[dict] = []
    negatives: dict = defaultdict(list)   # (strategy, scala) -> top-1 scores
    positive_top1: dict = defaultdict(list)
    routed_types: dict = {}

    for pair in pairs:
        route = classify_query(pair["query"])
        routed_types[pair["qa_id"]] = route.query_type
        for strategy in strategies:
            t0 = _time.time()
            if strategy == "routed_rr":
                # reranker SOLO su query non-code (il router decide anche qui)
                if route.query_type == "code":
                    hits = search(client, pair["query"], "routed", limit=LIMIT)
                else:
                    from src.retrieval.rerank import RERANK_TOPK, rerank

                    base = search(client, pair["query"], "routed",
                                  limit=RERANK_TOPK)
                    hits = rerank(pair["query"], base, limit=LIMIT)
            else:
                hits = search(client, pair["query"], strategy, limit=LIMIT)
            latency[strategy].append(_time.time() - t0)
            scale = strategy if strategy != "routed" else (
                "sparse" if route.query_type == "code" else "rrf")
            top1 = hits[0].score if hits else 0.0
            if pair["query_type"] == "negative":
                negatives[(strategy, scale)].append(round(top1, 4))
                continue
            rank = first_relevant_rank(hits, pair["expected"])
            positive_top1[(strategy, scale)].append(round(top1, 4))
            rows.append({"qa_id": pair["qa_id"], "strategy": strategy,
                         "lang": pair["lang"], "query_type": pair["query_type"],
                         "rank": rank})

    overall = aggregate(rows, "all")
    per_lang = aggregate(rows, "lang")
    per_type = aggregate(rows, "query_type")

    # misses del routed, per la lettura qualitativa
    misses = sorted({r["qa_id"] for r in rows
                     if r["strategy"] == "routed" and r["rank"] is None})

    threshold: dict = {}
    for (strategy, scale), negs in sorted(negatives.items()):
        pos = positive_top1.get((strategy, scale), [])
        threshold[f"{strategy}/{scale}"] = {
            "neg_top1_max": max(negs) if negs else None,
            "neg_top1": negs,
            "pos_top1_min": min(pos) if pos else None,
            "pos_top1_mediana": round(sorted(pos)[len(pos) // 2], 4) if pos else None,
            "separabile": bool(negs and pos and max(negs) < min(pos)),
        }

    lat_stats = {s: round(sorted(v)[len(v) // 2], 3)
                 for s, v in latency.items() if v}
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({
        "date": str(date.today()), "pairs": len(pairs),
        "indexed_points": len(payloads), "eval_set": str(EVAL_SET.name),
        "overall": overall, "per_lang": per_lang, "per_type": per_type,
        "routed_misses": misses, "threshold_analysis": threshold,
        "overlap_lessicale": overlap_stats, "latenza_mediana_s": lat_stats,
    }, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[eval] latenza mediana per strategia (s): {lat_stats}")

    # ------------------------------------------------------ report md (solo v0)
    if _TAG != "v0":
        for s, g in overall.items():
            m = g["-"] if "-" in g else list(g.values())[0]
            print(f"  {s:10s} R@1={m['recall@1']} R@5={m['recall@5']} "
                  f"MRR={m['MRR']}")
        print(f"[eval] risultati: {OUT_JSON}")
        return 0
    n_pos = sum(1 for p in pairs if p["query_type"] != "negative")
    lines = [
        "# P2.4 — Metriche di retrieval (recall@k per lingua) — DoD P2",
        "",
        f"> Data: {date.today()} · Corpus indicizzato: **{len(payloads)} chunk-riga** "
        "(Mamoli P231+P060 IT, DeliTools EN, Electrolux EN+ES) · Eval set: "
        f"**{n_pos} query positive + {len(pairs) - n_pos} negative** "
        "(`data/eval/eval_set_v0.jsonl`).",
        "",
        "**Limiti dichiarati:** eval set `draft-sintetico` (scritto su base GT, da "
        "validare con un tecnico esperto — TODO); **DE non coperto** (NordCap non "
        "ancora ingerito: richiede la generalizzazione dell'ingestion PDF); "
        "la **correttezza data l'evidenza** si misura a P3 (P2 è senza generazione "
        "LLM per vincolo di progetto). Un hit = la riga giusta (codice esatto, o "
        "doc+pos per le righe DeliTools senza codice) nei primi k risultati.",
        "",
    ]
    lines += md_table(overall, "Complessivo (tutte le query positive)")
    lines += md_table(per_lang, "Per lingua della query")
    lines += md_table(per_type, "Per tipo di query")
    lines += ["### Analisi soglia «non lo so» (top-1 score, per scala)",
              "",
              "| strategia/scala | max negativo | min positivo | mediana positivi | separabile |",
              "|---|---:|---:|---:|---|"]
    for k, t in threshold.items():
        lines.append(f"| {k} | {t['neg_top1_max']} | {t['pos_top1_min']} | "
                     f"{t['pos_top1_mediana']} | "
                     f"{'SI' if t['separabile'] else 'NO (overlap)'} |")
    lines += ["",
              f"Query positive senza hit nei top-10 (strategia routed): "
              f"{', '.join(misses) if misses else 'nessuna'}.",
              "",
              "### Criterio reranker (deciso, non applicato)",
              "Il reranker BGE entra in P2.5 **solo se** il gap "
              "recall@10−recall@1 della strategia scelta resta rilevante "
              "(>0.1) dopo il routing: altrimenti è latenza senza guadagno.",
              ""]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print(f"[eval] risultati: {OUT_JSON}")
    print(f"[eval] report:    {OUT_MD}")
    for s, g in overall.items():
        m = g["-"] if "-" in g else list(g.values())[0]
        print(f"  {s:7s} R@1={m['recall@1']} R@5={m['recall@5']} MRR={m['MRR']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
