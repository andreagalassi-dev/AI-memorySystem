# P2.4 — Metriche di retrieval (recall@k per lingua) — DoD P2

> Data: 2026-07-03 · Corpus indicizzato: **32775 chunk-riga** (Mamoli P231+P060 IT, DeliTools EN, Electrolux EN+ES) · Eval set: **54 query positive + 6 negative** (`data/eval/eval_set_v0.jsonl`).

**Limiti dichiarati:** eval set `draft-sintetico` (scritto su base GT, da validare con un tecnico esperto — TODO); **DE non coperto** (NordCap non ancora ingerito: richiede la generalizzazione dell'ingestion PDF); la **correttezza data l'evidenza** si misura a P3 (P2 è senza generazione LLM per vincolo di progetto). Un hit = la riga giusta (codice esatto, o doc+pos per le righe DeliTools senza codice) nei primi k risultati.

### Complessivo (tutte le query positive)

| strategia | gruppo | n | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---:|---:|---:|---:|---:|---:|
| dense | - | 54 | 0.333 | 0.407 | 0.444 | 0.463 | 0.382 |
| routed | - | 54 | 0.704 | 0.833 | 0.852 | 0.907 | 0.778 |
| rrf | - | 54 | 0.463 | 0.741 | 0.778 | 0.833 | 0.612 |
| sparse | - | 54 | 0.574 | 0.685 | 0.741 | 0.778 | 0.633 |

### Per lingua della query

| strategia | gruppo | n | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---:|---:|---:|---:|---:|---:|
| dense | EN | 14 | 0.429 | 0.429 | 0.5 | 0.571 | 0.455 |
| dense | ES | 6 | 0.5 | 0.667 | 0.667 | 0.667 | 0.583 |
| dense | IT | 34 | 0.265 | 0.353 | 0.382 | 0.382 | 0.316 |
| routed | EN | 14 | 0.714 | 0.714 | 0.786 | 0.857 | 0.744 |
| routed | ES | 6 | 0.833 | 0.833 | 0.833 | 0.833 | 0.833 |
| routed | IT | 34 | 0.676 | 0.882 | 0.882 | 0.941 | 0.782 |
| rrf | EN | 14 | 0.429 | 0.571 | 0.714 | 0.714 | 0.532 |
| rrf | ES | 6 | 0.667 | 0.833 | 0.833 | 0.833 | 0.75 |
| rrf | IT | 34 | 0.441 | 0.794 | 0.794 | 0.882 | 0.621 |
| sparse | EN | 14 | 0.429 | 0.643 | 0.643 | 0.643 | 0.512 |
| sparse | ES | 6 | 0.667 | 0.667 | 0.833 | 1.0 | 0.729 |
| sparse | IT | 34 | 0.618 | 0.706 | 0.765 | 0.794 | 0.667 |

### Per tipo di query

| strategia | gruppo | n | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---:|---:|---:|---:|---:|---:|
| dense | code | 16 | 0.0 | 0.0 | 0.0 | 0.0 | 0.0 |
| dense | mixed | 8 | 0.125 | 0.375 | 0.375 | 0.375 | 0.25 |
| dense | natural | 30 | 0.567 | 0.633 | 0.7 | 0.733 | 0.621 |
| routed | code | 16 | 0.938 | 1.0 | 1.0 | 1.0 | 0.969 |
| routed | mixed | 8 | 0.875 | 1.0 | 1.0 | 1.0 | 0.938 |
| routed | natural | 30 | 0.533 | 0.7 | 0.733 | 0.833 | 0.633 |
| rrf | code | 16 | 0.25 | 0.812 | 0.812 | 0.875 | 0.531 |
| rrf | mixed | 8 | 0.625 | 0.75 | 0.75 | 0.75 | 0.688 |
| rrf | natural | 30 | 0.533 | 0.7 | 0.767 | 0.833 | 0.636 |
| sparse | code | 16 | 0.75 | 0.875 | 0.875 | 0.875 | 0.792 |
| sparse | mixed | 8 | 0.75 | 0.75 | 0.75 | 0.875 | 0.762 |
| sparse | natural | 30 | 0.433 | 0.567 | 0.667 | 0.7 | 0.515 |

### Analisi soglia «non lo so» (top-1 score, per scala)

| strategia/scala | max negativo | min positivo | mediana positivi | separabile |
|---|---:|---:|---:|---|
| dense/dense | 0.6899 | 0.5287 | 0.6864 | NO (overlap) |
| routed/rrf | 0.5833 | 0.5 | 0.6667 | NO (overlap) |
| routed/sparse | 0.0 | 10.3305 | 15.9156 | SI |
| rrf/rrf | 0.5833 | 0.5 | 0.5 | NO (overlap) |
| sparse/sparse | 25.9111 | 10.3305 | 20.2529 | NO (overlap) |

Query positive senza hit nei top-10 (strategia routed): Q035, Q036, Q041, Q043, Q053.

### Criterio reranker (deciso, non applicato)
Il reranker BGE entra in P2.5 **solo se** il gap recall@10−recall@1 della strategia scelta resta rilevante (>0.1) dopo il routing: altrimenti è latenza senza guadagno.
