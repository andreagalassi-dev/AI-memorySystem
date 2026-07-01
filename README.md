# AI Knowledge Engine Manifatturiero

RAG ibrido verticale per il manifatturiero (manuali tecnici, tabelle ricambi, esplosi).
**Fase attuale: Fase 0 — locale, single-tenant.** Specifica e roadmap completa in
[`docs/SPEC.md`](docs/SPEC.md).

> Principio non negoziabile: il sistema deve essere **giusto, oppure dichiarare "non lo so" —
> mai sbagliato in silenzio.** Ogni risposta cita la fonte (pagina/documento).

## Stack (Fase 0)

- **Container:** Docker + docker-compose — servizi: `backend`, `qdrant`, `ocr`, `ui`.
- **LLM locale:** [Ollama](https://ollama.com) (headless, integrazione container).
- **Vector DB:** Qdrant. **DB di supporto:** PostgreSQL (da P4). **UI PoC:** Streamlit (da P5).
- **Ambiente Python:** [`uv`](https://docs.astral.sh/uv/).
- **Lingue del corpus:** IT / EN / DE.

## Setup ambiente (locale)

```bash
# 1. Ambiente Python con uv
uv sync --extra dev

# 2. Configurazione (NON committare .env)
cp .env.example .env
# poi modifica .env con i tuoi valori

# 3. Ollama (LLM locale)
#    Installa da https://ollama.com, quindi scarica un modello testo:
ollama pull qwen3:14b
```

## Struttura repo

```
src/backend/   entrypoint backend, client Qdrant + LLM, health
src/ocr/       servizio OCR (scheletro; pipeline in P1)
src/ui/        UI Streamlit (scheletro; in P5)
src/common/    modelli dati e util condivisi
scripts/       verifiche infra e catalogazione corpus
corpus/        corpus reale del cliente — NON committato
data/          output locali (catalogo, indici) — NON committato
docs/SPEC.md   architettura + roadmap P0–P7
tests/         test
```

## Roadmap

Si procede **un Punto alla volta** (P0 → P7), con ok esplicito tra un Punto e il successivo.
**P1 è un GATE critico** (validazione OCR/estrazione tabelle sul corpus reale). Dettaglio in
[`docs/SPEC.md`](docs/SPEC.md).

Stato: **P0 in corso** — Fondamenta & corpus reale.
