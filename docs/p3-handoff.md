# Handoff P2 → P3 — per la prossima istanza

> **P2 UFFICIALMENTE CHIUSO** (P2.1–P2.8, vedi `docs/p2-metrics.md`,
> `docs/p26-ingestion.md`, `docs/p27-visual.md`, `docs/p28-global-recall.md`).
> Regole di progetto invariate: **un Punto alla volta, plan mode prima del
> codice, OK esplicito dell'utente prima di iniziare P3**, micro-step con stop.
> Data handoff: 2026-07-03. Ultimo commit P2: `5426c4a`.

## 1. Architettura dati (NON toccare l'ingestion in P3)

**Qdrant v1.18.2** (pinnato in docker-compose, container `aike_qdrant`,
client 1.18.0). Due collection:

- **`aike_chunks`** — 32.966 punti testuali (54 PDF + 3 fogli XLSX):
  - named dense `dense` (1024, cosine) = **BGE-M3 via FlagEmbedding**
    (`src/ingestion/embedding.py`, loader lazy; input = SOLO descrizione
    riparata via `repair_desc`, mai metadati);
  - named sparse `sparse` (modifier **IDF**) = **BM25 fastembed `Qdrant/bm25`**
    (lingua stemmer `italian`, identica indice/query; input = pos+codice+desc).
    NON è lo sparse learned di BGE-M3: il token-codice deve restare ATOMICO;
  - payload: chunk completo (code, pos, desc, qty, price, doc, page/sheet,
    lang, type=table_row|text, status, warnings, source_cells, desc_search)
    + **payload index su `code`/`lang`/`doc`/`status`** (il filtro su `code`
    è il gancio del guardrail);
  - 1.514 punti senza vettore denso (chunk senza desc: solo lessicali).
- **`aike_pages_visual`** — 1.035 pagine grafiche: multivettori ~755 patch ×
  128 dim, **MaxSim su DOT** (mai cosine: amplifica i patch vuoti), HNSW
  `m=0` (brute-force), modello **ColQwen2 via transformers NATIVO**
  (`vidore/colqwen2-v1.0-hf`, `src/retrieval/visual.py`). Payload:
  doc/cat/page/render (PNG in `output/visual/renders/`, pronti per la UI).

**Normalizzazione multi-brand**: `config/brands.json` (3 profili espliciti:
mamoli con rejoin GT-validato, electrolux, delitools + **default
PASSTHROUGH** che rileva e non inventa; `src/ingestion/brands.py`). Stesso
registro alimenta router e chunker. Guardie attive in ingestion:
`code_not_in_source` (0 violazioni su 33k), contratto GT 1:1 sui CSV,
riconciliazione con l'audit P1. Rigenerabile: `classify_pages.py` →
`extract_corpus.py` → `chunk_corpus.py` → `ingest_chunks.py` →
`index_visual.py` (tutti checkpointati/resumabili; `output/` e `data/` sono
gitignored ma i file chiave sono committati con -f).

## 2. Il motore di ricerca (`src/retrieval/`)

**Router deterministico** (`router.py`, unit-testato, `reason` auditabile):
- **Tier A**: token che matcha una famiglia-codice del registro brands.json
  (V0+10 Mamoli, 0US Electrolux, numerico-9 DeliTools);
- **Tier B**: marker espliciti IT/EN/ES ("codice", "part number", "art.",
  "ref" NON c'è — nota sotto) + token con cifre, oppure query a token
  singolo con cifre;
- **visual**: marker visivi IT/EN/DE/ES ("esploso", "wiring",
  "Explosionszeichnung", "despiece"…), conserva l'eventuale code_token;
- classi: `code` → sparse-only **sul SOLO code_token** (le parole-marker
  affogherebbero il codice nei chunk-testo: bug misurato e fixato in P2.6);
  `mixed` → RRF con sparse sul token + dense sulla frase; `natural` → RRF;
  `visual` → collection visiva con **doc_hint_filter** (token query →
  stem documento) + lessicale sul codice se presente.

**Reranker** (`rerank.py`, ADOTTATO): BGE-reranker-v2-m3 con
**AutoModelForSequenceClassification nativo** (FlagReranker è ROTTO con
transformers 5.x). Strategia di produzione **`routed_rr`** in `search.py`:
cross-encoder sui top-20 routed, **MAI sulle query `code`** (il router
decide). Latenza mediana 0.764s vs 0.058s (dichiarata, da rivedere a P5).
Entry point unificato: `search_unified(client, query)`.

## 3. Baseline metriche (DoD P2 — eval v1 AVVERSARIO, 33k distrattori)

Eval `data/eval/eval_set_v1.jsonl`: 78 coppie, overlap lessicale
query↔target **mediana 0** (provato dal validatore), molte cross-lingua.
Strategia `routed_rr`:

| | R@1 | R@5 | R@10 | MRR |
|---|---:|---:|---:|---:|
| complessivo (70 pos.) | 0.486 | 0.600 | — | 0.542 |
| **code** (n=19) | **0.842** | — | **0.947** | 0.895 |
| **mixed** (n=8) | **0.875** | — | — | 0.875 |
| natural (n=43) | 0.256 | — | 0.419* | 0.324 |
| IT 0.316 · EN 0.625 · DE 0.667 · ES 0.750 · FR 1.000 | | | | |

\*R@10 natural dal routed base. Canale visivo (15 query pagina-level):
R@1 0.200, R@3 0.533, MRR 0.367. **Tallone dichiarato**: naturali IT
cross-lingua su descrizioni EN terse — le leve sono PROPRIO di P3
(query expansion/traduzione via LLM, arricchimento desc via VLM, glossario).
Il confronto v0 (0.944 su 234 chunk) misura l'asticella, non una regressione.

## 4. I GANCI per i guardrail P3 (misurati, pronti all'uso)

1. **Soglia "non lo so" sui codici — DETERMINISTICA**: query code → canale
   sparse BM25; codice inesistente → **top-score 0.0**; minimo positivo
   osservato **11.17** (v1) / 6.04 (v0). Separazione perfetta su entrambe le
   eval: `score == 0 → "codice non a catalogo"` è un guardrail a costo zero.
2. **Tutte le altre scale hanno OVERLAP** (dense, RRF, reranker: max neg
   0.5-0.73 vs min pos 0.09-0.50): il "non lo so" sulle naturali RICHIEDE
   segnali multipli (SPEC §F.2) — accordo testo↔visione, presenza citazione,
   margine tra top-1 e top-2. Numeri grezzi in `output/eval/results_v1.json`.
3. **Chunk `ocr_source`** (warnings, 3 pagine kbs vector-text): la guardia
   anti-invenzione è CIRCOLARE su testo OCR → il guardrail P3 deve degradarli
   a **"verifica manuale" obbligatoria**. Idem `status=degraded` con
   `warnings` (qty/price azzerati, desc_missing): campi None = mai inventati.
4. **Payload index su `code`**: il guardrail deterministico P3 ("ogni codice
   citato esiste letteralmente nella tabella recuperata") si implementa con
   filtro esatto Qdrant, senza LLM. I reject P2 (`_rejects.jsonl`) elencano i
   codici NON indicizzati per riga fusa → risposta "verifica manuale".
5. **Router.reason**: ogni decisione di routing è già spiegabile → va nel
   log di audit della risposta.
6. **Fusion testo↔visione da costruire** (P3): `search_unified` già ritorna
   entrambi i canali separati; l'accordo doc/pagina tra hit testuali e visivi
   è un segnale di confidenza previsto dalla SPEC e mai implementato.

## 5. Il mandato di P3 (SPEC §C, §D-validazione, §F.2)

**In P3 NON si tocca l'ingestion.** Si accende l'LLM (Ollama locale, famiglia
Qwen3 14B/32B come da SPEC §L0 — scelta finale DA TESTARE sul corpus) dentro
un'**orchestrazione a grafo LangGraph**: router (seme già pronto) → pipeline
(testuale `routed_rr` / visiva / ibrida) → **validazione deterministica**
(guardrail codici NON-LLM con unit test sui "quasi giusti") → sintesi con
**citazione obbligatoria** (doc+pagina, i payload la contengono già) →
percorso **"non lo so"** su segnali oggettivi (ganci §4), MAI
sull'auto-confidenza dell'LLM. DoD P3: risposta con fonte+pagina, codici
allucinati BLOCCATI, "non lo so" misurato (silenzio giusto vs pigrizia).

## Lezioni operative (pagate care, non ripeterle)

- **Dipendenze che falliscono in silenzio** (3 casi): docling `do_ocr=True`
  senza easyocr installato non OCRa nulla; colpali-engine con transformers
  5.x inizializza i LoRA random (leggere SEMPRE il load report; test
  bianca-vs-target come canone); FlagReranker usa API rimosse → **usare
  sempre il porting transformers nativo** quando esiste.
- I contratti fail-fast hanno intercettato corruzioni reali 4 volte
  (M6*16→M616, codici fantasma, eval rotto, regressione R@1): mantenerli.
- `uv remove`/`uv sync` senza `--extra dev` disinstalla pytest/ruff.
- Il file `docs/PITCH_DECK.md` è dell'utente, non committato: chiedere.

## Puntatori

- Report P2: `docs/p2-metrics.md` (v0), `docs/p26-ingestion.md`,
  `docs/p27-visual.md`, `docs/p28-global-recall.md`.
- Eval: `data/eval/eval_set_v1.jsonl` + `eval_visual_v0.jsonl` (gitignored),
  risultati in `output/eval/results_v1.json`, `p27_visual_results.json`.
- GT: `data/ground_truth/`, `data/page_labels/labels_v0.csv` (committato).
- Config: `config/brands.json`. Codice: `src/ingestion/`, `src/retrieval/`.
- Debiti con owner: fusione+non-lo-so multi-segnale+correttezza (P3),
  validazione eval dal tecnico (cliente), cross-lingua (P3+), normalizzatore
  a DB per-tenant (P7).
