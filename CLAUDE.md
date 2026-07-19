# Progetto: AI Knowledge Engine Manifatturiero — istruzioni per Claude Code

## Cos'è
RAG ibrido verticale per il manifatturiero (manuali tecnici, tabelle ricambi, esplosi).
Fase attuale: **Fase 0 locale, single-tenant**. La specifica e la roadmap completa sono in
`docs/SPEC.md`. IMPORTANT: leggi `docs/SPEC.md` prima di pianificare qualunque cosa.

## Principio non negoziabile
IMPORTANT: il sistema deve essere **giusto, oppure dichiarare "non lo so" — mai sbagliato in
silenzio.** L'evoluzione futura (Epica H) prevede: **"mai un'affermazione senza fonte"**.
Ogni risposta cita la fonte (pagina/documento). Nessun codice pezzo va mostrato se
non esiste nel dato strutturato recuperato (guardrail deterministico non-LLM, vedi SPEC §L2).

## Come lavoriamo
- Il piano operativo vivo è `docs/piano_implementazione/15_piano_prodotto.md` (epiche
  A→H). Si procede **un punto alla volta**, con DoD e verifica del reviewer. YOU MUST
  NOT iniziare un'epica nuova senza l'ok esplicito dell'utente.
- Usa sempre la **plan mode** prima di scrivere codice: mostrami il piano, aspetta ok, poi esegui.
- A fine punto: confronta il risultato col DoD, fai un commit, e fermati.
- La roadmap a fasi P0-P7 (SPEC) è COMPLETATA fino a P5 e superata dal piano epiche:
  è storia, non guida operativa.

## Nota storica ingestion
Le sfide di ingestion individuate sul corpus reale sono archiviate in
`docs/archive/ingestion-challenges.md` (note pre-gate P1, oggi superate: l'architettura
di ingestion reale è quella descritta nello Stack). Il ridisegno dell'estrazione
massiva è materia dell'epica C del piano.

## Stack reale
- Container: Docker + docker-compose. Servizi: backend, qdrant, ocr, ui.
- Ingestion: Fast-Dispatch + TextTableExtractor (born-digital) / GLM-OCR (scansioni).
- LLM locale: MLX Gemma-4-26B come cervello via multiplexer. Fallback: Ollama.
- Embedding: BGE-M3 + BM25 (testo) e Qwen3-VL 1024d (visivo).
- Orchestrazione: LangGraph tri-intent (structured, visual, narrative) con fallback structured->retrieval.
- Vector DB: Qdrant. DB di supporto: PostgreSQL.
- UI: Chainlit (auth, sessioni, feedback nativi). Streamlit resta solo come demo congelata.
- Qualità: suite con ~721 test. Gate anti-regressione via `scripts/smoke_e2e.py`.

## Lingue del corpus
IT / EN / DE. Le metriche di valutazione vanno calcolate **separatamente per lingua**.

## Convenzioni codice
- Python, ambiente isolato (uv/venv). Ogni nodo del grafo testabile in isolamento.
- Il guardrail codici è codice non-LLM con unit test (inclusi casi di codici "quasi giusti").
- Niente segreti nel repo: config via `.env` non committato.
- Commit piccoli e descrittivi, uno per sotto-task quando possibile.

## Cosa NON fare ora (rimandato a P7)
YOU MUST NOT implementare: cloud, multi-tenancy oltre la collezione-per-tenant, batching
multi-tenant, integrazione ERP live, SSO. Fuori scopo per la Fase 0.
```
