# Handoff — Fase 1.3 Cervello Unificatore (per la prossima istanza)

> Branch: `feature/fase1-pipeline`. Spec completa: `docs/superpowers/specs/2026-07-09-fase1.3-cervello-unificatore-design.md`.
> Leggi anche `src/graph/pipeline.py` (il grafo) e le memorie `fase1.1-pixelrag.md` / `fase1.2-estrazione-massiva.md`.

## STATO: Fase 1.3 COMPLETA AL 100% — orchestratore a tre rami su adapter REALI

Il Cervello Unificatore e' **chiuso end-to-end**: tutti e tre i rami sono cablati con
adapter reali e verificati (unit test + grounding loop col modello/DB veri). Nessuna
regressione: full suite **626 passed** + i 3 rossi ambientali noti (Qdrant-corpus:
`test_rbac` TestLeakAsimmetrico ×2, `test_search_filter` — conteggi punti su corpus
non caricato, indipendenti dal grafo).

- **STRUCTURED**: `PgSqlExecutor` su ruolo Postgres **read-only** (`aike_ro`) + render
  `OllamaModel` (qwen2.5:14b, decoding deterministico). Grounding loop reale: ALTA,
  prezzo ancorato, doppia barriera PASS.
- **VISUAL**: `QdrantVisualIndex` + `ColQwen2Embedder` sulla collection reale
  `aike_pages_visual` (1035 pagine). Grounding loop col modello vero: RBAC asimmetrico
  end-to-end (tecnico e commerciale ricevono set disgiunti, zero leak).
- **NARRATIVE**: RAG testuale preesistente.

Ogni adapter e' cablato in `_default_deps` in **try/except fail-safe**: se il backend
manca, il ramo degrada a NARRATIVE (mai crash).

Commit chiave: `50b45f0` (PgSqlExecutor RO), `96c8d1f` (OllamaModel + STRUCTURED on),
`6e2d66e` (QdrantVisualIndex + VISUAL on). Test infra: `a0e0301` (gate ruolo RO + init-db).

### Tech-debt / migliorie future (NON bloccanti — Fase 1.3 e' chiusa)
1. **MLXModel**: adapter alternativo a `OllamaModel` sullo stesso Port `LanguageModel`
   (Gemma/Nemotron via mlx-lm). Drop-in, zero impatto sul grafo. Oggi il render usa
   Ollama (verificabile subito); MLX resta scelta di performance su Apple Silicon.
2. **Re-ingestion visiva tile-level**: `aike_pages_visual` e' page-level (P2.7). Il
   `pixelrag/ingest.py` supporta tile-level con payload RBAC; una re-ingestion darebbe
   granularita' tile + `pages_from_tiles` non-degenere. Non necessaria: il ramo VISUAL
   funziona gia' su dato reale page-level.
3. **Connection pool / leak** (`_default_deps`): oggi si apre una connessione Postgres
   RO per `answer()` (TODO gia' nel codice). Da gestire quando la UI girera' in continuo.
4. **doc_hint_filter** (P2.7): filtro deterministico per nome-doc, miglioria di recall
   sul ramo VISUAL, non ancora portata nel percorso pixelrag.

## 1. Design (approvato dall'utente)

Evoluzione **ADDITIVA** del grafo LangGraph P3.5 (`GraphState` TypedDict, `Deps` con
callable iniettati, nodi puri, fail-closed, `answer(query, role, deps=None)`). I nuovi
campi di stato e i nuovi `Deps` sono opzionali: con `None` il ramo NON e' cablato e
l'intent degrada al RAG testuale (NARRATIVE) — zero regressione.

```
                        router_node
                    (classify_query + derive_intent)  ← deterministico, NO LLM
                             │
        ┌────────────────────┼────────────────────┐
   intent=STRUCTURED     intent=VISUAL         intent=NARRATIVE
    (se llm+sql_query)    (se visual_search)     (default fail-safe)
        │                    │                        │
     sql_node            visual_node             expansion
   Text-to-Parameters   PixelRAG 1.1                 │
   lookup righe_listino  NESSUN LLM              retrieval
        │                puntatore det.               │
     render_node          MEDIA                   confidence
   LLM FORMATTA i dati     │                    (+fallback v2/eliminato)
   Barriera #2 (prezzo)    │                        │
        │                  │                     fusion→synthesis
        └──────────────────┴────────► guardrail ◄────┘   Barriera #1 (codici, P3.1)
                                          │
                                      finalize (reconcile fail-closed)
```

- **Router deterministico**: `derive_intent(route, query)` (in `src/retrieval/router.py`)
  da `classify_query` (`code/visual/natural/mixed` + `code_token`) + whitelist keyword
  prezzo (`STRUCTURED_KEYWORDS`, IT/EN/DE). Le keyword di QUANTITA' sono volutamente
  ESCLUSE (nessuna colonna quantita' in `righe_listino` → sarebbe un falso). `visual`
  prevale; `code_token` + (route `code` o keyword-prezzo) → STRUCTURED; resto → NARRATIVE.
- **Convergenza invariata**: tutti i rami finiscono su **guardrail (P3.1) → finalize
  (P3.2)** = Defense in Depth. Il test tri-intent lo prova: un codice allucinato sul
  ramo SQL viene bloccato dal guardrail **dopo** aver superato la Barriera #2.
- **Confidence per ramo**: STRUCTURED (lookup esatto SSOT) → ALTA; VISUAL (similarita'
  MaxSim, non lookup) → MEDIA (l'occhio umano valida l'immagine); NARRATIVE → calibrata
  P3.2. Fail-closed ovunque: eccezione I/O → `system_error`→BASSA; 0 risultati → BASSA→REFUSAL.

## 2. Ramo STRUCTURED (Passi 2+4)

- `src/graph/tools/sql_tool.py` — **Text-to-Parameters, MAI Text-to-SQL**. Tripla
  barriera anti-injection: `codice_normalizzato` via `codes.canonical` (riduce a
  `[0-9A-Z]`) passato SOLO come placeholder `%s`; `campo` SOLO da whitelist chiusa
  (`ALLOWED_COLUMNS`) composta con `psycopg.sql.Identifier`; nessuna interpolazione di
  stringa. Tabella unica: `estrazione.righe_listino`. Port `SqlExecutor` +
  `FakeSqlExecutor` + `PgSqlExecutor` (conn iniettata, schema param).
- `src/graph/render.py` — l'LLM **formatta solo** i dati letti (mai genera SQL).
  **Barriera #2 (`verify_price_grounding`)**: value-grounding deterministico — ogni
  prezzo enunciato dev'essere un valore del DB e almeno uno enunciato, altrimenti BASSA.
- `pipeline.py`: `sql_node` (extract_params→run→Evidence, ALTA), `render_node`
  (LLM→grounding→citazioni deterministiche dalle righe). Deps: `llm`, `sql_query`.

## 3. Ramo VISUAL (Passo 5)

- `src/graph/tools/visual_tool.py` — **NESSUN LLM**: `format_visual_answer(pages)`
  restituisce un puntatore deterministico col **pattern-citazione canonico**
  `(doc, pag. N)` (lo stesso di `synthesis`), cosi' il frontend lo parsa con la stessa
  regex (`extract_citations`) per iniettare l'immagine, e il guardrail resta PASS.
  `visual_citations(pages)`; `make_visual_search(embedder, index, k=3)` → callable
  `(query, role)` che chiude su `pixelrag.visual_search` (RBAC+versioning fail-closed).
- `pipeline.py`: `visual_node` (role→visual_search; eccezione→system_error; 0 pagine o
  RBAC-filtra-tutto→BASSA; pagine→MEDIA). Deps: `visual_search`. State: `visual_pages`.

## 4. Test

- Dietro i Fake (DB/GPU/MLX-free): `test_graph_sql_tool.py` (20), `test_router_intent.py`
  (18), `test_graph_render.py` (17), `test_graph_structured.py` (13),
  `test_graph_visual_tool.py` (8), `test_graph_visual.py` (8), `test_graph_ollama_model.py`
  (3), `test_graph_triintent.py` (7 — contratto e2e tri-intent).
- Integrazione adapter reali: `test_sql_tool_pg.py` (`needs_pg` — gate ruolo read-only +
  wiring `_default_deps`), `test_pixelrag_qdrant_index.py` (`needs_qdrant` — RBAC no-leak
  su collection usa-e-getta + asimmetria RBAC su dato reale + wiring).

## 5. Adapter reali — CABLATI (`_default_deps`, tutti fail-safe)

1. **`PgSqlExecutor`** (STRUCTURED) su ruolo Postgres **read-only** `aike_ro`. Il ruolo
   nasce con l'init-db (`docker/init-db/10-readonly-role.sh`, solo volume nuovo); sul
   volume dev esistente e' stato creato a mano (stesso SQL). Difesa in profondita': il
   DB rifiuta INSERT/UPDATE/DELETE (provato in `test_sql_tool_pg.py`).
2. **`OllamaModel`** (`LanguageModel`, render STRUCTURED) su `qwen2.5:14b`, decoding
   deterministico (`SAFE_OPTIONS`: temp 0.1 + seed). MLXModel resta drop-in futuro (§debt).
3. **`QdrantVisualIndex` + `ColQwen2Embedder`** (VISUAL) sulla collection reale
   `aike_pages_visual`. (NB: l'embedder reale del progetto e' **ColQwen2**, non
   "Qwen3VL" come indicava una vecchia nota.)

### Riferimenti utili nel repo
- `src/ingestion/codes.py::canonical` — normalizzazione codici (riusata).
- `src/ingestion/extraction/load.py` — pattern `psycopg.sql.Identifier` + schema param.
- `src/common/roles.py::Role` (StrEnum) — filtro RBAC obbligatorio, fail-closed.
- Schema 1.2: `estrazione.righe_listino(doc_stem, page, codice_normalizzato,
  codice_originale, descrizione, prezzo, updated_at)`.
