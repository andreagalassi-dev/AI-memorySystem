# Progetto: AI Knowledge Engine Manifatturiero — istruzioni per Claude Code

## Cos'è
RAG ibrido verticale per il manifatturiero (manuali tecnici, tabelle ricambi, esplosi).
Fase attuale: **Fase 0 locale, single-tenant**. La specifica e la roadmap completa sono in
`docs/SPEC.md`. IMPORTANT: leggi `docs/SPEC.md` prima di pianificare qualunque cosa.

## Principio non negoziabile
IMPORTANT: il sistema deve essere **giusto, oppure dichiarare "non lo so" — mai sbagliato in
silenzio.** Ogni risposta cita la fonte (pagina/documento). Nessun codice pezzo va mostrato se
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

## Stack deciso (dettaglio in SPEC §L0)
- Container: Docker + docker-compose. Servizi: backend, qdrant, ocr, ui.
- LLM locale: Ollama / LM Studio. Hardware disponibile: Apple Silicon + NVIDIA RTX.
- OCR: Marker (default); al gate testare anche MinerU e Docling.
- Embedding: BGE-M3 (dense+sparse+multivector). Reranker: BGE-reranker-v2.
- VLM: Qwen3-VL 8B (32B per OCR tedesco difficile).
- Ragionatore (solo testo): famiglia Qwen3 14B/32B. Orchestrazione: LangGraph.
- Vector DB: Qdrant (collezione per tenant). DB di supporto: PostgreSQL. UI PoC: Streamlit.

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
