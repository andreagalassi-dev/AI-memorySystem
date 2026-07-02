# P2.4 — Metriche di retrieval (recall@k per lingua) — DoD P2

> Data: 2026-07-02 · Corpus indicizzato: **234 chunk-riga** (Mamoli P231+P060 IT, DeliTools EN, Electrolux EN+ES) · Eval set: **54 query positive + 6 negative** (`data/eval/eval_set_v0.jsonl`).

**Limiti dichiarati:** eval set `draft-sintetico` (scritto su base GT, da validare con un tecnico esperto — TODO); **DE non coperto** (NordCap non ancora ingerito: richiede la generalizzazione dell'ingestion PDF); la **correttezza data l'evidenza** si misura a P3 (P2 è senza generazione LLM per vincolo di progetto). Un hit = la riga giusta (codice esatto, o doc+pos per le righe DeliTools senza codice) nei primi k risultati.

### Complessivo (tutte le query positive)

| strategia | gruppo | n | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---:|---:|---:|---:|---:|---:|
| dense | - | 54 | 0.593 | 0.667 | 0.685 | 0.722 | 0.636 |
| routed | - | 54 | 0.944 | 1.0 | 1.0 | 1.0 | 0.972 |
| rrf | - | 54 | 0.852 | 1.0 | 1.0 | 1.0 | 0.926 |
| sparse | - | 54 | 0.963 | 0.963 | 0.963 | 0.981 | 0.966 |

### Per lingua della query

| strategia | gruppo | n | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---:|---:|---:|---:|---:|---:|
| dense | EN | 14 | 0.714 | 0.714 | 0.714 | 0.714 | 0.714 |
| dense | ES | 6 | 0.667 | 0.833 | 0.833 | 0.833 | 0.75 |
| dense | IT | 34 | 0.529 | 0.618 | 0.647 | 0.706 | 0.583 |
| routed | EN | 14 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| routed | ES | 6 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| routed | IT | 34 | 0.912 | 1.0 | 1.0 | 1.0 | 0.956 |
| rrf | EN | 14 | 0.929 | 1.0 | 1.0 | 1.0 | 0.964 |
| rrf | ES | 6 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| rrf | IT | 34 | 0.794 | 1.0 | 1.0 | 1.0 | 0.897 |
| sparse | EN | 14 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| sparse | ES | 6 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| sparse | IT | 34 | 0.941 | 0.941 | 0.941 | 0.971 | 0.945 |

### Per tipo di query

| strategia | gruppo | n | R@1 | R@3 | R@5 | R@10 | MRR |
|---|---|---:|---:|---:|---:|---:|---:|
| dense | code | 16 | 0.0 | 0.0 | 0.062 | 0.125 | 0.025 |
| dense | mixed | 8 | 0.375 | 0.75 | 0.75 | 0.875 | 0.554 |
| dense | natural | 30 | 0.967 | 1.0 | 1.0 | 1.0 | 0.983 |
| routed | code | 16 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| routed | mixed | 8 | 0.875 | 1.0 | 1.0 | 1.0 | 0.938 |
| routed | natural | 30 | 0.933 | 1.0 | 1.0 | 1.0 | 0.967 |
| rrf | code | 16 | 0.625 | 1.0 | 1.0 | 1.0 | 0.812 |
| rrf | mixed | 8 | 0.875 | 1.0 | 1.0 | 1.0 | 0.938 |
| rrf | natural | 30 | 0.967 | 1.0 | 1.0 | 1.0 | 0.983 |
| sparse | code | 16 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| sparse | mixed | 8 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| sparse | natural | 30 | 0.933 | 0.933 | 0.933 | 0.967 | 0.938 |

### Analisi soglia «non lo so» (top-1 score, per scala)

| strategia/scala | max negativo | min positivo | mediana positivi | separabile |
|---|---:|---:|---:|---|
| dense/dense | 0.6059 | 0.4373 | 0.6581 | NO (overlap) |
| routed/rrf | 0.6667 | 0.5 | 1.0 | NO (overlap) |
| routed/sparse | 0.0 | 6.039 | 8.306 | SI |
| rrf/rrf | 0.6667 | 0.5 | 0.75 | NO (overlap) |
| sparse/sparse | 8.4843 | 0.0 | 11.4423 | NO (overlap) |

Query positive senza hit nei top-10 (strategia routed): nessuna.

### Criterio reranker (deciso, non applicato)
Il reranker BGE entra in P2.5 **solo se** il gap recall@10−recall@1 della strategia scelta resta rilevante (>0.1) dopo il routing: altrimenti è latenza senza guadagno.
