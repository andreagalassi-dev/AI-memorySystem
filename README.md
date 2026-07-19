# AI Knowledge Engine Manifatturiero

RAG ibrido verticale per il manifatturiero (manuali tecnici, tabelle ricambi, esplosi).
**Fase attuale: Fase 0 — locale, single-tenant.** Specifica e roadmap completa in
[`docs/SPEC.md`](docs/SPEC.md).

Il sistema funziona in locale (Fase 0), elabora il corpus, e tramite un grafo LangGraph tri-intent
fornisce risposte citando sempre la fonte. Il "cervello" sfrutta un multiplexer per gestire
modelli MLX su Mac o fallback via Ollama, con embedding ibridi e visivi.

> Principio non negoziabile: il sistema deve essere **giusto, oppure dichiarare "non lo so" —
> mai sbagliato in silenzio.** Ogni risposta cita la fonte (pagina/documento).
> Evoluzione futura: **"mai un'affermazione senza fonte"**.

## Riservatezza dati

Tutto il flusso è locale; sono previsti flag offline per installazioni air-gapped (`HF_HUB_OFFLINE`, `TRANSFORMERS_OFFLINE`, `CHAINLIT_NO_TELEMETRY`).

## Stack reale

- **Container:** Docker + docker-compose — servizi: `backend`, `qdrant`, `ocr`, `ui`.
- **Cervello LLM:** MLX Gemma-4-26B (Mac) via multiplexer / Ollama fallback.
- **Ingestion:** Fast-Dispatch + TextTableExtractor (born-digital) / GLM-OCR (scansioni).
- **RAG & Vector DB:** Grafo LangGraph tri-intent, embedding BGE-M3+BM25 e Qwen3-VL. Qdrant + PostgreSQL per le fonti.
- **UI:** Chainlit con autenticazione e feedback.
- **Ambiente Python:** [`uv`](https://docs.astral.sh/uv/).

## Setup e avvio ambiente (locale)

```bash
# 1. Ambiente Python con uv
uv sync --extra dev  # L'extra 'demo' e' richiesto finche' la demo permanente Streamlit vive

# 2. Configurazione (NON committare .env)
cp .env.example .env
# Compila i valori nel .env. Le flag offline sono disabilitate per default ma attive nel template.

# 3. Avvio applicazione (con preambolo environment)
source .venv/bin/activate
chainlit run src/ui/chainlit_app.py -w

# 4. Smoke test (verifica della pipeline di base)
python scripts/smoke_e2e.py
```

## Struttura repo

```
src/backend/   grafo LangGraph, multiplexer, logic
src/ocr/       servizio OCR
src/ui/        app Chainlit principale
src/common/    modelli dati, schema feedback, utils
scripts/       smoke tests e utility varie (es. smoke_e2e.py)
corpus/        corpus reale del cliente — NON committato
data/          output locali (catalogo, indici) — NON committato
docs/          documentazione, SPEC.md, piani operativi (es. piano_implementazione/)
tests/         suite con ~721 test
```

## Roadmap

Si procede per Epiche e Punti come da piano operativo `docs/piano_implementazione/15_piano_prodotto.md`.
L'epica A (Un solo sistema) è integrata; il cuore del sistema è in produzione su main.
