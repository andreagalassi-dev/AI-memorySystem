# Benchmark Cervello A/B/D — Metriche Citazioni e Latenza

> **Nota di allineamento (25/08/2026):** questo è il benchmark storico che ha portato alla
> decisione di produzione (A9.4, commit `c7a6249`): backend **B** (`ollama:gemma4:26b-mlx`,
> MTP, `think=False`) è il cervello di produzione, con **D** (`ollama:qwen2.5:14b`) come
> **fallback**, non alternativa equivalente — anche se qui D misura correttezza/citazioni pari
> o migliori di B su questo set ridotto (8 query), non è il modello scelto come primario.
> Coerente con CLAUDE.md ("Cervello: Gemma 4 26B via Ollama... fallback qwen2.5:14b").

Questo documento riporta i risultati del benchmark eseguito sui tre backend candidati per il "cervello unificatore" (LLM) del sistema AIKE, misurando la stabilità delle citazioni, la correttezza, i rifiuti e la latenza (senza caching).

## Setup
- **Query set**: 8 query totali (3 codice, 4 naturali, 1 tecnica fuori dominio).
- **Esecuzione**: Ogni query è stata eseguita 3 volte in sequenza per backend per catturare il comportamento non deterministico.
- **Backend valutati**:
  - **A**: `mlx` (Stato attuale: `mlx-community/gemma-4-26b-a4b-it-4bit` con fallback su Ollama `qwen2.5:14b`)
  - **B**: `ollama:gemma4:26b-mlx` (MTP via Ollama locale, con `think=False` per evitare l'erosione del budget num_predict da parte del channel reasoning nascosto)
  - **D**: `ollama:qwen2.5:14b` (Modello di controllo via Ollama locale)

---

## 1. Risultati Aggregati per Backend

| Backend | Correttezza (Fonte OK) | Citazioni Presenti | Rifiuti | Latenza (p50 / max) | Fallback (MLX) |
|---|---|---|---|---|---|
| **A (MLX gemma-4-26b)** | 83.3% (15/18) | 75.0% (18/24) | 25.0% | 6.00s / 42.57s | 0.0% |
| **B (Ollama gemma4:26b-mlx)** | 100.0% (18/18) | 87.5% (21/24) | 12.5% | 6.39s / 138.95s | 0.0% |
| **D (Ollama qwen2.5:14b)** | 100.0% (18/18) | 100.0% (24/24) | 0.0% | 6.29s / 47.88s | 0.0% |

> *Nota*: La "Correttezza" indica quante volte la fonte attesa (documento e pagina specifici) è stata esplicitamente citata nella risposta, calcolata solo sulle query con fonte nota a priori.

---

## 2. Risultati per Classe di Query

### Classe: Codice (3 query × 3 run)

| Backend | Correttezza | Citazioni Presenti | Rifiuti | Latenza (p50 / max) |
|---|---|---|---|---|
| **A** | 100.0% | 100.0% | 0.0% | 3.31s / 3.72s |
| **B** | 100.0% | 100.0% | 0.0% | 1.93s / 2.70s |
| **D** | 100.0% | 100.0% | 0.0% | 5.07s / 6.45s |

### Classe: Naturale (4 query × 3 run)

| Backend | Correttezza | Citazioni Presenti | Rifiuti | Latenza (p50 / max) |
|---|---|---|---|---|
| **A** | 66.7% | 50.0% | 50.0% | 9.05s / 42.57s |
| **B** | 100.0% | 75.0% | 25.0% | 9.20s / 138.95s |
| **D** | 100.0% | 100.0% | 0.0% | 10.13s / 43.68s |

### Classe: Tecnica (1 query × 3 run)

| Backend | Citazioni Presenti | Rifiuti | Latenza (p50 / max) |
|---|---|---|---|
| **A** | 100.0% | 0.0% | 22.91s / 23.03s |
| **B** | 100.0% | 0.0% | 20.71s / 28.73s |
| **D** | 100.0% | 0.0% | 32.49s / 47.88s |

*(Nessuna fonte specifica attesa per la query tecnica fuori dominio)*

---

## Appendice: Numeri Grezzi

I dump completi dei JSON contenenti le metriche disaggregate e le latenze per singola run sono archiviati nella root del progetto:
- `scripts/benchmark_raw_mlx.json`
- `scripts/benchmark_raw_ollama_gemma4_26b-mlx.json`
- `scripts/benchmark_raw_ollama_qwen2.5_14b.json`

### Dati storici invalidi

> Il primo run del backend B era INVALIDO perché il modello generava testo nel thinking channel consumando l'intero budget di `num_predict=512`. Per questo, la correttezza misurata era pari allo 0-16%. Con l'aggiunta di `think=False` nel client, il benchmark riflette le prestazioni corrette (riportate sopra).

Vecchi risultati B (INVALIDA, thinking non gestito):
- Correttezza: 16.7%
- Citazioni presenti: 12.5%
- Latenza p50/max: 18.95s / 54.81s
