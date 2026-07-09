# Handoff — Fase 1.3 Cervello Unificatore (per la prossima istanza)

> Branch: `feature/fase1-pipeline`. Spec completa: `docs/superpowers/specs/2026-07-09-fase1.3-cervello-unificatore-design.md`.
> Leggi anche `src/graph/pipeline.py` (il grafo) e le memorie `fase1.1-pixelrag.md` / `fase1.2-estrazione-massiva.md`.

## STATO: Fase 1.3 (logica) COMPLETA — Passi 1→6 committati

L'architettura logica del Cervello Unificatore e' **chiusa e testata dietro i Fake**.
Residuo: **solo il wiring degli adapter reali** (vedi §5). Nessuna regressione: full
suite **verde salvo i 3 rossi ambientali noti** (Qdrant-corpus: `test_rbac`
TestLeakAsimmetrico ×2, `test_search_filter` — asseriscono conteggi punti su una
collection non caricata, indipendenti dal grafo).

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

## 4. Test (tutti DB/GPU/MLX-free, dietro i Fake)

- `test_graph_sql_tool.py` (20), `test_router_intent.py` (18), `test_graph_render.py`
  (17), `test_graph_structured.py` (13), `test_graph_visual_tool.py` (8),
  `test_graph_visual.py` (8), `test_graph_triintent.py` (7 — contratto e2e tri-intent).

## 5. PROSSIMO COMPITO — wiring adapter reali (l'orchestratore e' pronto)

`_default_deps` in produzione ha oggi `llm=None`, `sql_query=None`, `visual_search=None`
→ tutto passa da NARRATIVE. Attaccare un adapter alla volta, ognuno col suo mini-gate:

1. **`PgSqlExecutor` reale (consigliato per primo — no GPU/serving)**: cablare in
   `_default_deps` con connessione al DB Fase 1.2 su **ruolo Postgres read-only**
   (difesa in profondita': anche se qualcosa aggirasse la whitelist, il DB rifiuta le
   scritture). Test `needs_pg` sulla query reale con **schema usa-e-getta** (pattern
   `tests/conftest.py` + `test_extraction_load.py`): seed di `righe_listino`, verifica
   lookup per `codice_normalizzato`, e un test che il ruolo read-only **respinge** un
   INSERT/UPDATE. Verificare che i `needs_pg` girino (marker in conftest).
2. **`MLXModel`** (`LanguageModel`) per il render STRUCTURED — serving MLX reale.
3. **`QdrantVisualIndex` + `Qwen3VLEmbedder`** (`VisualIndex`/`VisualEmbedder`) per
   VISUAL — residuo noto di 1.1.

### Riferimenti utili nel repo
- `src/ingestion/codes.py::canonical` — normalizzazione codici (riusata).
- `src/ingestion/extraction/load.py` — pattern `psycopg.sql.Identifier` + schema param.
- `src/common/roles.py::Role` (StrEnum) — filtro RBAC obbligatorio, fail-closed.
- Schema 1.2: `estrazione.righe_listino(doc_stem, page, codice_normalizzato,
  codice_originale, descrizione, prezzo, updated_at)`.
