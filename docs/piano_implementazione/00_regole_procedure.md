# Regole, Procedure e Principi Architetturali

Questo documento definisce le regole di base, l'architettura di deployment e le procedure operative per lo sviluppo del sistema AI-memorySystem.

## 1. Architettura di Deployment e Hardware
> **Architettura di Deployment:** Il sistema core (AI + Database) risiede su un Server dedicato (es. Mac Studio o Server GPU aziendale). Gli utenti finali (tecnici, commerciali) non installano nulla in locale, ma accedono al sistema tramite una semplice e veloce Pagina Web / Web App aziendale.
> **Gestione Hardware e Watchdog:** Per evitare che il server si blocchi (Out Of Memory o Thermal Throttling) quando elabora massivamente i PDF, il sistema integra un "Watchdog". Questo componente monitora costantemente RAM e temperatura, scaricando i modelli inattivi dalla memoria prima di caricarne di nuovi (Model Multiplexing dinamico).

## 2. Strategia Modelli AI (Standard di Progetto)
Il sistema adotta una **gerarchia di modelli** che scala in base all'hardware:

| Ruolo | Modello Primario Locale | Futuro (Produzione/Server dedicati) |
|-------|------------------------|------------------------------------|
| **Cervello / Ragionatore** | Google Gemma 4 (12B o 26B MoE) con Multi-Token Prediction (tramite `mlx`) | NVIDIA Nemotron-Cascade-2 (30B MoE) o Modelli >70B su GPU |
| **Estrazione Tabelle** | GLM-OCR (3B) — ~3 GB a Q4 | MinerU 2.5 / olmOCR-2 |
| **Retrieval Visivo** | PixelRAG (Qwen3-VL-Embedding) | — |

**Ottimizzazione Hardware-Aware:**
- **Apple Silicon (Mac Locale):** Utilizzo nativo di **MLX** (`mlx-lm`) per modelli MoE. Grazie all'altissima banda passante della Memoria Unificata Apple, l'architettura MoE scala perfettamente in locale abbattendo la latenza e mantenendo un'esecuzione fluida in Q4.
- Il context window dell'LLM primario va limitato a ~8-16K in locale per risparmiare KV cache (~2-4 GB), consentendo di tenere residenti in RAM anche gli embedder (PixelRAG).

## 3. Controllo Qualità AI (Testing di Regressione e MLOps)
Durante tutto lo sviluppo, il sistema implementa un meccanismo di **Testing di Regressione**.
*   **Golden Dataset e Gate:** Comando unico `python scripts/golden_gate.py` basato su una baseline versionata (`data/golden/baseline/golden_baseline_v1.json`).
*   **Metriche ed Esecuzione:** Misura l'accuratezza con metriche separate per lingua (IT/EN/DE) ed esegue test di stabilità (×3 run per le risposte non puramente estrattive).
*   **Gate di Rilascio:** La modifica è approvata solo se non presenta regressioni rispetto alla baseline (es. una query corretta che inizia a fallire o degrada in stabilità). Introduce **hard floors index-aware** per il livello di affidabilità (precision ALTA), bloccando il merge in caso di allucinazioni documentate. Qualsiasi regressione interrompe l'esecuzione con exit status non nullo.

## 4. Procedure Operative e Regole di Sviluppo (Enrichment)
Per garantire un livello Enterprise del codice, tutto il team deve aderire alle seguenti prassi:

### 4.1 Git Workflow
- **Branching:** Utilizzare il *Feature Branch Workflow*. Il branch `main` deve sempre essere stabile e deployabile. Sviluppare le nuove fasi in branch isolati (es. `feature/fase1-pipeline`).
- **Commit:** Utilizzare i *Conventional Commits* (es. `feat: aggiunta estrazione pydantic`, `fix: risolto OOM su mac`).
- **Pull Request:** Nessun push diretto su `main`. Le modifiche devono passare la suite di test automatizzati (CI) prima del merge.

### 4.2 Standard del Codice (Python)
- **Typing:** Type hints obbligatori per tutte le funzioni e metodi.
- **Linting & Formatting:** Utilizzo rigoroso di `ruff` (configurato in `pyproject.toml` per linea a 100 caratteri).
- **Docstrings:** Ogni classe core (es. Estrattori OCR, Nodi del Grafo) deve avere docstring descrittive su input e output.

### 4.3 Gestione dei Dati Sensibili
- I file `.env` non devono **mai** essere committati.
- L'esposizione di API Key (Tavily, eventuale OpenAI/Anthropic di fallback) deve passare rigorosamente per `pydantic-settings`.
