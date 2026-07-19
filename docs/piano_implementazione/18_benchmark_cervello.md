# Benchmark Cervello A/B/D — Metriche Citazioni e Latenza

Questo documento riporta i risultati del benchmark eseguito sui tre backend candidati per il "cervello unificatore" (LLM) del sistema AIKE, misurando la stabilità delle citazioni, la correttezza, i rifiuti e la latenza (senza caching).

## Setup
- **Query set**: 8 query totali (3 codice, 4 naturali, 1 tecnica fuori dominio).
- **Esecuzione**: Ogni query è stata eseguita 3 volte in sequenza per backend per catturare il comportamento non deterministico.
- **Backend valutati**:
  - **A**: `mlx` (Stato attuale: `mlx-community/gemma-4-26b-a4b-it-4bit` con fallback su Ollama `qwen2.5:14b`)
  - **B**: `ollama:gemma4:26b-mlx` (MTP via Ollama locale)
  - **D**: `ollama:qwen2.5:14b` (Modello di controllo via Ollama locale)

---

## 1. Risultati Aggregati per Backend

| Backend | Correttezza (Fonte OK) | Citazioni Presenti | Rifiuti | Latenza (p50 / max) | Fallback (MLX) |
|---|---|---|---|---|---|
| **A (MLX gemma-4-26b)** | 83.3% (15/18) | 75.0% (18/24) | 25.0% | 6.00s / 42.57s | 0.0% |
| **B (Ollama gemma4:26b-mlx)** | 16.7% (3/18) | 12.5% (3/24) | 0.0% | 18.95s / 54.81s | 0.0% |
| **D (Ollama qwen2.5:14b)** | 100.0% (18/18) | 100.0% (24/24) | 0.0% | 6.29s / 47.88s | 0.0% |

> *Nota*: La "Correttezza" indica quante volte la fonte attesa (documento e pagina specifici) è stata esplicitamente citata nella risposta, calcolata solo sulle query con fonte nota a priori.

---

## 2. Risultati per Classe di Query

### Classe: Codice (3 query × 3 run)

| Backend | Correttezza | Citazioni Presenti | Rifiuti | Latenza (p50 / max) |
|---|---|---|---|---|
| **A** | 100.0% | 100.0% | 0.0% | 3.31s / 3.72s |
| **B** | 33.3% | 33.3% | 0.0% | 15.66s / 16.65s |
| **D** | 100.0% | 100.0% | 0.0% | 5.07s / 6.45s |

### Classe: Naturale (4 query × 3 run)

| Backend | Correttezza | Citazioni Presenti | Rifiuti | Latenza (p50 / max) |
|---|---|---|---|---|
| **A** | 66.7% | 50.0% | 50.0% | 9.05s / 42.57s |
| **B** | 0.0% | 0.0% | 0.0% | 26.14s / 54.81s |
| **D** | 100.0% | 100.0% | 0.0% | 10.13s / 43.68s |

### Classe: Tecnica (1 query × 3 run)

| Backend | Citazioni Presenti | Rifiuti | Latenza (p50 / max) |
|---|---|---|---|
| **A** | 100.0% | 0.0% | 22.91s / 23.03s |
| **B** | 0.0% | 0.0% | 20.57s / 30.38s |
| **D** | 100.0% | 0.0% | 32.49s / 47.88s |

*(Nessuna fonte specifica attesa per la query tecnica fuori dominio)*

---

## Appendice: Numeri Grezzi

I dump completi dei JSON contenenti le metriche disaggregate e le latenze per singola run sono archiviati nella root del progetto:
- `scripts/benchmark_raw_mlx.json`
- `scripts/benchmark_raw_ollama_gemma4_26b-mlx.json`
- `scripts/benchmark_raw_ollama_qwen2.5_14b.json`
