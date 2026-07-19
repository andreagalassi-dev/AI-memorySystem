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
- Si procede **un Punto della roadmap alla volta** (P0, P1, …). YOU MUST NOT iniziare il Punto
  successivo senza il mio ok esplicito.
- **P1 è un GATE**: se la qualità OCR/estrazione tabelle non regge sul corpus reale, fermati e
  segnalalo. Non proseguire a P2.
- Usa sempre la **plan mode** prima di scrivere codice: mostrami il piano, aspetta ok, poi esegui.
- A fine Punto: confronta il risultato col DoD del Punto, fai un commit, e fermati.

## Note per il gate P1
IMPORTANT: le sfide di ingestion individuate sul corpus reale (parser tabelle,
immagini/schemi, chunking, dual-indexing, multilingua) sono registrate in
`docs/ingestion-challenges.md`. Sono **note pre-gate, non decisioni**:
l'architettura di ingestion si fissa a P1/P2 **dopo** lo spike di estrazione
reale. Leggile prima di pianificare P1.

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
