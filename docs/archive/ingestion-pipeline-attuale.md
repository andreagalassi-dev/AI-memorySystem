# Pipeline di ingestion ATTUALE (manuale) — mappa per l'auto-ingestion

> **Scopo (step B1 del piano).** Prima di automatizzare l'ingestion (Fase B),
> serve capire *esattamente* quali comandi si eseguono oggi a mano per portare un
> PDF dal file grezzo fino ai vettori in Qdrant e alla riga in Postgres. Questo
> documento è **solo descrittivo** (nessuna modifica al codice). Per ogni passo:
> **script**, **input**, **output**, e soprattutto se **ricostruisce tutto** o
> **lavora sul singolo file** — perché è questa la distinzione che B2/B3/B4 devono
> risolvere.
>
> Fonti lette: `scripts/chunk_tables.py`, `scripts/ingest_chunks.py`,
> `scripts/normalize_codes_docling.py`, `scripts/backfill_qdrant.py`,
> `src/ingestion/embedding.py`, `src/ingestion/chunking.py` (i 6 file richiesti da
> B1), più — per completare i passi «estrazione/OCR» e «registrazione Postgres» che
> non stanno in quei 6 — `scripts/extract_corpus.py`, `scripts/chunk_corpus.py`,
> `scripts/registry_populate.py`, `src/registry/registry.py`.

---

## Vista d'insieme — l'ordine reale dei comandi

La pipeline oggi è **una catena di script CLI a scala di corpus**, non un percorso
per singolo documento. L'ordine effettivo (dopo `docker compose up -d postgres qdrant`
e `source .venv/bin/activate`):

| # | Fase | Script | Granularità |
|---|------|--------|-------------|
| 0 | Catalogazione formati/lingue | `scripts/catalog_corpus.py` | intero corpus |
| 0 | Classificazione pagine (grafiche vs testuali) → route | `scripts/classify_pages.py` | intero corpus |
| 1 | **Estrazione / OCR** (PDF → Markdown) | `scripts/extract_corpus.py` (Docling) | per-pagina, guidato da route (batch corpus) |
| 2 | **Normalizzazione codici** | `scripts/normalize_codes_docling.py` (spike) / dentro il chunking a scala corpus | spike: lista hardcoded; corpus: dentro il chunk |
| 3 | **Chunking tabelle** | `scripts/chunk_tables.py` (spike) + `scripts/chunk_corpus.py` (corpus) | per-tabella / per-documento estratto |
| 4 | **Embedding + upsert Qdrant** | `scripts/ingest_chunks.py` | **DROP+CREATE globale** della collezione |
| 4b | Embedding visivo pagine grafiche | `scripts/index_visual.py` | collezione visiva |
| 5 | **Registrazione versione/ruoli Postgres** | `scripts/registry_populate.py` | legge da Qdrant, popola Postgres |
| 5b | Proiezione chiavi SSOT su Qdrant | `scripts/backfill_qdrant.py` | merge additivo per stem |

**Osservazione centrale per la Fase B:** oggi **Postgres è derivato da Qdrant**
(passo 5 legge gli stem da Qdrant e li registra), poi il passo 5b riscrive su
Qdrant le chiavi di ruolo/versione. Cioè i vettori nascono **senza** ruolo né stato
versione nel payload, che arrivano solo in un secondo giro. Per l'ingestion di un
**singolo** documento questa inversione va raddrizzata: il ruolo dev'essere noto
*prima* e scritto nel payload al momento dell'upsert (vedi §"Implicazioni per B2").

---

## Passo 1 — Estrazione / OCR (PDF → Markdown)

- **Script:** `scripts/extract_corpus.py` (env Docling isolato via PEP 723:
  `docling`, `pymupdf`). Variante spike per le tabelle Mamoli: `scripts/extract_docling.py`.
- **Input:**
  - un **file di route** (`output/spike/page_classes/_route_docling.txt`) prodotto da
    `classify_pages.py`, con righe `categoria/doc.pdf#pN` (quali pagine mandare a Docling);
  - i PDF sotto `corpus/`.
- **Cosa fa:** converte **pagina per pagina** (PDF monopagina temporaneo, converter
  riusato) con `do_ocr=False` + `TableFormer ACCURATE` (architettura decisa al gate P1).
  Checkpoint immediato per pagina + `_progress.jsonl` append/flush → rilancio = resume.
- **Output:** `output/extraction/<doc_stem>/p<NNNN>.md` (un markdown per pagina) +
  `output/extraction/_progress.jsonl`.
- **Ricostruisce tutto o singolo file?** **Batch di corpus guidato dal route**: itera
  su tutti i documenti/pagine del route. È *resume-safe* (salta le pagine già estratte),
  ma **non** è pensato come «estrai questo solo PDF»: dipende dal route pre-calcolato e
  dalla classificazione pagine a monte.

## Passo 2 — Normalizzazione codici (deterministica, auditabile)

- **Script:** `scripts/normalize_codes_docling.py` (**solo spike**). A scala corpus la
  normalizzazione è **incorporata nel chunking** (`chunk_corpus.py` → `corpus_chunks.chunk_page`
  usa il profilo brand).
- **Input:** `output/spike/docling/raw/<table_id>/<table_id>.md`. Lista tabelle
  **hardcoded**: `TABLES = ["MAMOLI_P231_IT", "MAMOLI_P060_IT"]`.
- **Cosa fa:** ricompone i codici spezzati/fusi con **regole deterministiche** (nessun
  LLM), profilo brand risolto da Postgres via `src.ingestion.brands.resolve(table_id)`
  (fail-closed se il DB profili è giù). Include il pass di riparazione «ultima ratio»
  (debito §4: row-rejoin + prefix-restore + confusable), tenuto separato dai safe.
- **Output:**
  - `output/spike/docling_normalized/raw/<table_id>/<table_id>.md` (md con codici canonici);
  - `<table_id>/codes_normalized.csv` (audit trail: original → normalized → type → reason);
  - `<table_id>/repaired_codes.jsonl` (ancore riparate, solo se presenti);
  - `_normalization_log.json`, `_code_accuracy.json` (codeAcc raw vs normalized vs GT).
- **Ricostruisce tutto o singolo file?** **Lista fissa hardcoded** (Mamoli). Non
  parametrizzato sul singolo documento arbitrario.

## Passo 3 — Chunking tabelle (un chunk = un record logico)

- **Script:** `scripts/chunk_tables.py` (spike) + `scripts/chunk_corpus.py` (corpus).
  Logica in `src/ingestion/chunking.py` (`chunk_table`), `src/ingestion/csv_chunks.py`
  (`chunk_csv_table`), `src/ingestion/corpus_chunks.py` (`chunk_page`).
- **Input:**
  - spike: markdown **normalizzato** (`output/spike/docling_normalized/raw/...`) + il CSV
    di audit di P1 + le ancore riparate. `TABLE_META`/`CSV_TABLES` **hardcoded**.
  - corpus: `output/extraction/<doc>/p*.md`; profilo brand per doc via `resolve(stem)`,
    lingua rilevata su testo aggregato; salta esplicitamente Mamoli P060/P231 (già coperte
    dai chunk-spike GT-validati → niente duplicati).
- **Cosa fa:** chunking **difensivo** — ogni chunk emesso è *giusto*, tutto ciò che non è
  ricostruibile va in una corsia di **reject** esplicita (mai indicizzato sbagliato in
  silenzio). Gestisce celle unite, tabelle multi-pagina, intestazioni ripetute, righe-totale.
  Assegna `parent_id` / `is_assembly` usando il pattern assembly del profilo brand.
- **Output:**
  - spike: `output/spike/chunks/<table_id>/chunks.jsonl` + `_rejects.jsonl` + `_chunking_report.json`;
  - corpus: `output/ingestion/chunks/<doc>.jsonl` + `_rejects.jsonl` + `_report.json`.
- **Campi del chunk** (dataclass `Chunk`): `chunk_id`, `text`, `code`, `code_raw`,
  `normalization_type`, `pos`, `desc`, `qty`, `price`, `doc`, `page`, `lang`, `table_index`,
  `row_index`, `segment`, `status`, `warnings`, `source_cells`, `sheet`, **`parent_id`**,
  **`is_assembly`**. ⚠️ Nota: la chiave di documento nel payload si chiama **`doc`**
  (nome file), **non** `doc_stem`.
- **Ricostruisce tutto o singolo file?** `chunk_corpus.py` **itera su tutti** i documenti
  in `output/extraction/`. Le *funzioni* sottostanti (`chunk_page`, `chunk_table`) sono
  però **per-documento/tabella** e riutilizzabili — è il punto d'aggancio per B2.

## Passo 4 — Embedding + upsert in Qdrant

- **Script:** `scripts/ingest_chunks.py`. Embedder in `src/ingestion/embedding.py`.
- **Input:** i chunk spike (`TABLE_IDS` hardcoded) **+** tutti i `output/ingestion/chunks/*.jsonl`.
- **Cosa fa:**
  1. carica i modelli: **dense BGE-M3** (1024-dim, `DENSE_NAME="dense"`) + **sparse BM25**
     (`SPARSE_NAME="sparse"`, modifier IDF). Text-prep pura: il denso vede solo la
     descrizione riparata; lo sparse vede pos + codice atomico + descrizione;
  2. **`delete_collection` + `create_collection`** della collezione `aike_chunks` →
     ricrea gli indici payload (`code`, `lang`, `doc`, `status`, `parent_id`, `is_assembly`);
  3. embedding di tutti i chunk, **upsert a lotti** da 64 (id punto deterministico
     `uuid5(chunk_id)`), payload = il chunk + `desc_search`;
  4. smoke test: canale lessicale (codice esatto → top-1), denso, ibrida RRF.
- **Output:** collezione Qdrant `aike_chunks` **ricreata da zero** con tutti i punti.
- **Ricostruisce tutto o singolo file?** ⛔ **DROP + CREATE GLOBALE ad ogni run.** La
  collezione è trattata come *dato derivato* rigenerabile dai `chunks.jsonl`. È
  **inutilizzabile** per aggiungere un singolo documento senza distruggere gli altri —
  è **il** motivo per cui B2 deve creare una funzione di upsert per-documento e B3 la
  cancellazione mirata per `doc_stem`.
- **NB payload ruoli/versione:** in questo passo **non** vengono scritti
  `ruoli_consentiti` né `version_status` — arrivano solo al passo 5b (backfill).

*(Passo 4b — `scripts/index_visual.py`: embedding visivo tipo ColPali per le sole pagine
grafiche → collezione `aike_pages_visual`. Stessa natura «batch», fuori dal cuore
codici/tabelle ma parte del quadro.)*

## Passo 5 — Registrazione versione/ruoli in Postgres (SSOT)

- **Script:** `scripts/registry_populate.py` → `src/registry/registry.py::register_document`.
- **Input:** legge **da Qdrant in sola lettura** gli stem distinti presenti in `aike_chunks`
  (payload `doc`/`cat`/`lang`) + il mapping ruoli default per categoria (`config/default_roles.json`).
- **Cosa fa:** per ogni stem chiama `register_document(conn, slug=stem, category=..., roles=...,
  doc_stem=stem, langs=...)` → crea documento logico + versione **v1/current** + ruoli.
  **IDEMPOTENTE**: se lo `doc_stem` è già registrato non modifica nulla.
- **Output:** tabelle Postgres `documents`, `document_versions` (stato `current`/`superseded`),
  `document_roles`.
- **Ricostruisce tutto o singolo file?** Itera su tutti gli stem di Qdrant, ma la funzione
  `register_document` è **per-documento e idempotente** — riutilizzabile direttamente da B2
  (con il ruolo passato esplicitamente, invece di derivarlo da Qdrant).

### Passo 5b — Proiezione chiavi SSOT → Qdrant

- **Script:** `scripts/backfill_qdrant.py` → `src/registry/sync.py::sync_collection(qc, coll, export)`.
- **Cosa fa:** legge la proiezione SSOT (`export_backfill(conn)`: `doc_stem → stato/versione/ruoli`)
  e fa **`set_payload` per stem** (merge additivo) delle 3 chiavi
  `version_status`, `doc_version`, `ruoli_consentiti` su `aike_chunks` e `aike_pages_visual`,
  ricreando gli indici keyword. Vettori e chiavi esistenti restano intatti. **IDEMPOTENTE.**
- **Ricostruisce tutto o singolo file?** Additivo per stem (patcha solo i divergenti), ma
  richiede l'`export` completo del registro.

---

## Copertura richiesta da B1 (checklist di verifica)

| Passo richiesto | Coperto da |
|---|---|
| (1) estrazione/OCR | `extract_corpus.py` (Docling, per-pagina, batch route) — Passo 1 |
| (2) chunking tabelle | `chunk_tables.py` + `chunk_corpus.py` (`chunk_table`/`chunk_page`) — Passo 3 |
| (3) normalizzazione codici | `normalize_codes_docling.py` (spike) / dentro chunking corpus — Passo 2 |
| (4) embedding + upsert Qdrant | `ingest_chunks.py` (BGE-M3 + BM25, **drop+create globale**) — Passo 4 |
| (5) registrazione versione/ruoli Postgres | `registry_populate.py` + `backfill_qdrant.py` — Passo 5/5b |

---

## Implicazioni per B2/B3/B4 (perché la pipeline attuale non basta)

Queste **non sono modifiche** (fuori scope B1): sono i vincoli emersi, che i prossimi
step dovranno affrontare.

1. **Nessun percorso per-documento.** L'unico entry-point di indicizzazione
   (`ingest_chunks.py`) fa **drop+create globale**. B2 deve costruire
   `ingest_document(filepath, doc_stem, role, conn, is_update)` che fa **solo upsert**
   dei punti di *quel* file, riusando `chunk_page`/`chunk_table` + `embedding.py` +
   `register_document` — senza toccare gli altri documenti.

2. **Ordine SSOT invertito.** Oggi Postgres è derivato da Qdrant, poi i ruoli tornano su
   Qdrant (5→5b). Per il singolo file il ruolo è noto a monte: va scritto nel payload
   (`ruoli_consentiti`, `version_status='current'`) **all'upsert**, e registrato in Postgres
   con `register_document(..., roles=[role])`. Niente doppio giro.

3. **Re-ingest lascia orfani.** Non esiste cancellazione mirata dei punti di un `doc_stem`
   prima di re-indicizzare → B3 deve aggiungere `delete(collection, Filter(doc_stem==...))`
   *prima* dell'upsert (idempotenza), senza toccare gli altri stem.

4. **Chiave payload da uniformare.** I chunk usano la chiave **`doc`** (nome file); il
   registro e il backfill ragionano su **`doc_stem`**. B2 dovrà scrivere un payload
   coerente (lo spec di B2 chiede esplicitamente `doc_stem` tra le chiavi) — da decidere se
   allineare o mantenere entrambe.

5. **Normalizzazione a scala singola.** A scala corpus la normalizzazione codici è dentro
   `chunk_page` (via profilo brand); lo script spike separato resta solo per Mamoli. Per un
   file nuovo qualsiasi, il percorso riutilizzabile è **chunking-con-profilo**, con
   fallback «default passthrough» se il brand non ha profilo (Fase C).
