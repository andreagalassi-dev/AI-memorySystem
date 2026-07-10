# Handoff P3 → P4 — stato dei dati e ganci per versioning/RBAC

> **P3 CHIUSO** (P3.1–P3.5). Data: 2026-07-04. Ultimo commit: `2698f65`.
> Regole invariate: un Punto alla volta, plan mode prima del codice, OK esplicito
> prima di iniziare P4, micro-step con stop. **P4 = Gestione dati, versioning,
> RBAC** (SPEC §E, §F.3). Questo doc serve a **non rompere la retrocompatibilità**.

## 1. Cosa è vivo oggi (P3)

Grafo LangGraph deterministico (`src/graph/pipeline.py`) come entry point:
`answer(query)` → router → retrieval(routed_rr) → confidence(P3.2) →
fusion(diagnostica) → synthesis(qwen2.5:14b) → guardrail(P3.1) → finalize.
Fail-closed di rete. Il retrieval e il guardrail **dipendono dal payload
Qdrant qui sotto**: modificarlo senza cura rompe il motore.

## 2. Qdrant — stato ESATTO (v1.18.2, container `aike_qdrant`)

### `aike_chunks` — 32.966 punti (testo)
- **dense** `dense`: size **1024**, distance **Cosine** (BGE-M3).
- **sparse** `sparse`: modifier **IDF** (BM25 fastembed).
- **payload (16 chiavi):** `chunk_id, doc, page, type, code, pos, desc,
  desc_search, qty, price, lang, cat, status, warnings, source_cells, text`.
  - `chunk_id` = **ID naturale gerarchico** `doc/pXXXX/tN/gN`
    (es. `stoll_CMS830C_spareparts/p0012/t1/g1`) — utile come chiave di versione.
  - `status` ∈ {`ok`, `degraded`} → **qualità di ingestion, NON versione**.
  - `warnings` = lista (es. `['table_without_code_column']`, `ocr_source`).
  - campi `None` reali dove il dato non esiste (qty/price/code su righe testo).
- **payload_indexes:** `doc`(keyword), `code`(keyword), `lang`(keyword),
  `status`(keyword). Il filtro esatto su **`code`** è il gancio del guardrail
  P3.1 — **non rimuoverlo/rinominarlo**.

### `aike_pages_visual` — 1.035 punti (pagine grafiche)
- **multivettore**: size **128**, distance **DOT**, MaxSim, HNSW `m=0`
  (brute-force) — ColQwen2.
- **payload (4 chiavi):** `doc, page, cat, render`
  (`render` = path PNG in `output/visual/renders/<doc>/pXXXX.png`).
- **payload_indexes:** `doc`(keyword), `page`(integer), `cat`(keyword).

> **Chiave di join testo↔visione (usata da P3.3):** entrambe le collection
> condividono lo **stesso stem `doc`** e una **`page` intera**. Qualsiasi schema
> di versioning deve preservare questa corrispondenza.

## 3. Ganci per il VERSIONING (SPEC §E) — cosa c'è / cosa manca

**Non esiste ancora versioning.** Ganci utili già presenti:
- **`doc`** (indicizzato) — l'unità naturale di versione è il documento.
- **`chunk_id`** gerarchico — ID stabile per marcare/sostituire per-chunk.
- Il campo **`status`** esiste già ma è **occupato** dalla qualità
  (`ok`/`degraded`): **NON riusarlo** per lo stato di versione (romperebbe il
  gancio #3 del guardrail e i filtri retrieval).

**Cosa manca (da aggiungere in P4, in modo additivo):**
- un campo nuovo tipo `version_status` ∈ {`current`, `superseded`} + eventuale
  `doc_version`/`valid_from` nel payload; **nuovo payload_index keyword** su
  `version_status` per filtrare "solo attuali" nel retrieval.
- regola SPEC §E: caricando una nuova versione, i chunk vecchi si **marcano**
  `superseded` (embedding **non cancellati**); l'AI consulta prima gli attuali;
  se trova solo nel sostituito → risponde **avvisando** (obsoleto).
- un **registro versioni** su PostgreSQL (già previsto in stack, non ancora
  usato) — mapping doc → versione corrente/storico.

## 4. Ganci per RBAC (SPEC §F.3) — cosa manca

**Non esiste ancora RBAC.** Il payload **non ha** campi di ruolo. Serve (P4):
- un campo `ruoli_consentiti` (list[keyword]) nel payload + **payload_index**;
- ogni query porta il ruolo → **filtro obbligatorio** su Qdrant
  (`ruoli_consentiti` contiene il ruolo). Punto d'innesto naturale: il
  `retrieval_node`/`_default_deps().retrieve` in `src/graph/pipeline.py` (oggi
  nessun filtro di ruolo).

## 5. Contratti da NON rompere (retrocompatibilità)

1. Chiavi payload e **indici** `code/doc/lang/status` (aike_chunks) e
   `doc/page/cat` (aike_pages_visual): il grafo e il guardrail li leggono.
2. `Evidence` in `src/graph/pipeline.py` mappa 1:1 dal payload
   (`doc,page,lang,code,desc,text,qty,price,type,status,warnings`) — se aggiungi
   campi al payload, aggiornali anche qui (additivo).
3. Il retrieval di produzione (`_default_deps().retrieve`) usa `routed_rr`
   (BM25 su code, reranker-sigmoid su natural/mixed). Un filtro versione/ruolo
   va aggiunto **come `query_filter`**, senza toccare lo scoring.
4. **Ingestion intoccata in P3** ed è la fonte del payload: la rigenerazione
   passa da `classify_pages.py → extract_corpus.py → chunk_corpus.py →
   ingest_chunks.py → index_visual.py` (checkpointati). P4 dovrà decidere se il
   versioning si applica in ingestion (re-index) o come patch di payload.

## 6. Debiti P3 aperti (non bloccanti per P4)

Promozione MEDIA→ALTA non adottata (serve 3° segnale indipendente);
`confidence.CALIBRATED_V1` soglie provvisorie (ri-fittare col tecnico);
guardrail conservativo su quantità in prosa; tallone naturali IT cross-lingua.

## 7. Puntatori

- Grafo: `src/graph/pipeline.py`; report `docs/p35-graph.md`.
- Guardrail/confidence/fusion/synthesis: `src/retrieval/{guardrail,confidence,
  fusion,synthesis}.py`; report `docs/p3{1,2,3,4}-*.md`.
- Spec di design: `docs/superpowers/specs/2026-07-0*-p3*-*.md`.
- Config: `config/brands.json`. SPEC completa: `docs/SPEC.md` (§E, §F.3 per P4).
- LLM: Ollama `qwen2.5:14b` (config `src/backend/config.py`).
