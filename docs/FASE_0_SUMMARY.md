# Fase 0 — Riepilogo (locale, single-tenant)

> **Stato:** ✅ **P0 completato e verificato end-to-end.** Prossimo passo: **P1 —
> Validazione ingestion (GATE CRITICO)**, non ancora iniziato.
> Roadmap completa in [`SPEC.md`](SPEC.md); sfide di ingestion per il gate in
> [`ingestion-challenges.md`](ingestion-challenges.md).

## Obiettivo della Fase 0
Avere un ambiente locale funzionante e il corpus reale (peggiore) di un cliente
sul disco, catalogato — **prima** di costruire qualunque pipeline. Principio
guida non negoziabile: il sistema deve essere **giusto, oppure dire "non lo so",
mai sbagliato in silenzio**.

## Stack messo in piedi
- **Container:** Docker + docker-compose — servizi `qdrant`, `backend`, `ocr`, `ui`.
- **LLM locale:** Ollama (headless). Modello di test: `qwen2.5:1.5b`.
- **Vector DB:** Qdrant (volume nominato `qdrant_storage`).
- **Backend:** FastAPI con `/health` che pinga Qdrant a cascata.
- **Ambiente Python:** `uv` (0.11.26). Script standalone con dipendenze inline PEP 723.

## Lavoro svolto, punto per punto

| Punto | Cosa | Commit |
|-------|------|--------|
| **P0.1** | Scaffolding repo + `.gitignore` + `pyproject.toml` (uv) + git init | `8b1634f` |
| **P0.2** | `docker-compose.yml` + 3 Dockerfile placeholder; rete `aike_net`, volume Qdrant | `10c29c0` |
| **P0.3** | `scripts/check_llm.py` — verifica Ollama (`/api/tags` + generazione reale su `/api/generate`) | `38c5a42`, `92b6541` |
| **P0.4** | Backend FastAPI: `config.py`, factory Qdrant, `GET /health` (200/503) | `8dd089d` |
| **—** | Tracciamento `uv.lock` per build riproducibili | `696e2e6` |
| **P0.5** | Corpus reale del cliente sul disco (6 file, gitignored) | — (dati locali) |
| **P0.6** | `scripts/catalog_corpus.py` — catalogo per formato/lingua → `data/corpus_catalog.csv` | `1b0d501` |
| **P0.6** | `docs/ingestion-challenges.md` — sfide registrate per il gate P1 | `a56082f` |
| **P0.7** | `scripts/smoke_test.py` — smoke test end-to-end auto-orchestrato | `bbc5a17` |

## Struttura attuale del progetto
```
AI-memorySystem/
├── CLAUDE.md                     istruzioni di lavoro + note per il gate P1
├── README.md                     setup uv + Ollama
├── docker-compose.yml            4 servizi su rete aike_net
├── pyproject.toml / uv.lock      deps Python (uv), lock tracciato
├── .env.example                  template config (Ollama + Qdrant)
├── docker/
│   ├── backend.Dockerfile        placeholder python:3.11-slim
│   ├── ocr.Dockerfile            placeholder
│   └── ui.Dockerfile             placeholder
├── src/
│   ├── backend/
│   │   ├── config.py             Settings pydantic (QDRANT_HOST/PORT da env)
│   │   ├── main.py               FastAPI app + GET /health
│   │   └── clients/
│   │       └── qdrant_client.py  factory QdrantClient cached
│   ├── ocr/                      scheletro (pipeline in P1)
│   ├── ui/                       scheletro (Streamlit in P5)
│   └── common/                   scheletro
├── scripts/
│   ├── check_llm.py              verifica LLM (P0.3)
│   ├── catalog_corpus.py         catalogazione corpus (P0.6)
│   └── smoke_test.py             smoke test E2E (P0.7)
├── docs/
│   ├── SPEC.md                   architettura + roadmap P0–P7
│   ├── ingestion-challenges.md   note pre-gate per P1/P2
│   └── FASE_0_SUMMARY.md         questo file
├── corpus/                       corpus reale — GITIGNORED (6 file)
├── data/                         output generati (catalogo) — GITIGNORED
└── tests/                        (smoke test infra vive in scripts/)
```

## Corpus reale catalogato (P0.6)
6 file (4 PDF + 2 XLSX), tutti i PDF risultati **nativi** (nessuno scansionato
nel campione — buon segnale per l'estrazione testuale a P1):

| File | Formato | Pagine | Lingue rilevate |
|------|---------|--------|-----------------|
| DeliTools_SpareParts_List.xlsx | xlsx | — | en |
| DirectKitchen_Forni_Schemi_IT_EN.pdf | pdf | 96 | it |
| Electrolux_Distinta_Ricambi.xlsx | xlsx | — | en |
| Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf | pdf | 237 | it, en |
| NordCap_Esploso_Ricambi_DE.pdf | pdf | 5 | de |
| Silca_TriaxPro_Ricambi_Multilingua.pdf | pdf | 4 | fr, es, ca |

Note: 3 PDF erano **AES-encrypted** (risolto aggiungendo `cryptography` a
`catalog_corpus.py`). `Silca` è multilingua reale (rilevate lingue diverse
sulle pagine campionate) → da tenere d'occhio al gate.

## Risultati dei test (osservati)
- **`check_llm.py`**: demone Ollama raggiungibile; generazione reale con
  `qwen2.5:1.5b` → risposta `'OK'`, exit 0. Ramo `ConnectionRefusedError` su
  porta chiusa → exit 1.
- **`/health` (P0.4)**: Qdrant **UP** → `HTTP 200` `{"backend":"ok","qdrant":"ok",...}`;
  Qdrant **DOWN** → `HTTP 503` `{"qdrant":"unreachable",...}`.
- **`smoke_test.py` (P0.7)**, end-to-end:
  - happy path → `[1/3]` docker running/healthy, `[2/3]` LLM `'OK'`,
    `[3/3]` `/health 200` → **SMOKE TEST PASSED, exit 0**;
  - caso LLM giù → `[2/3]` ❌ con messaggio chiaro → **SMOKE TEST FAILED, exit 1**
    (fail-fast, teardown eseguito).

## Decisioni tecniche degne di nota
- **Container backend/ui restano placeholder**: il backend si testa in locale via
  `uv`/uvicorn; i placeholder **non pubblicano porte** (il docker-proxy teneva
  host:8000 aperto senza servire HTTP, ingannando i controlli e bloccando
  uvicorn). Le porte tornano quando i container serviranno davvero.
- **`QDRANT_HOST`/`QDRANT_PORT` da env**: stesso codice gira in locale
  (`localhost`) e in container (`qdrant` sulla rete `aike_net`).
- **Rilevamento backend via probe HTTP** (`/health`), non socket raw.

## DoD di P0 — verifica
> *I container salgono, l'inferenza locale risponde, il corpus reale è sul disco
> e catalogato per formato/lingua.*

✅ **Soddisfatto e verificato end-to-end** dallo smoke test.

## Prossimo passo
**P1 — Validazione ingestion (⚠️ GATE CRITICO).** Va superato prima di costruire
altro: misurare qualità OCR e accuratezza estrazione tabelle sul corpus reale,
con decisione go/no-go scritta. Leggere prima `docs/ingestion-challenges.md`.
Non iniziare senza OK esplicito.
