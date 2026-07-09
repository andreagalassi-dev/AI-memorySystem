# Handoff — Fase 1.3 Cervello Unificatore (per la prossima istanza)

> Branch: `feature/fase1-pipeline`. Spec completa: `docs/superpowers/specs/2026-07-09-fase1.3-cervello-unificatore-design.md`.
> Leggi anche `src/graph/pipeline.py` (grafo P3.5 che stiamo evolvendo) e le memorie `fase1.1-pixelrag.md` / `fase1.2-estrazione-massiva.md`.

## 1. Riassunto del Design Architetturale (approvato dall'utente)

La Fase 1.3 **evolve in modo ADDITIVO** il grafo LangGraph maturo di P3.5
(`src/graph/pipeline.py`: `GraphState` TypedDict, `Deps` con callable iniettati,
nodi puri, fail-closed, `answer(query, role, deps=None)`). Nessuna regressione: i
nuovi campi di stato e i nuovi `Deps` sono opzionali; con `None` il grafo resta
quello di oggi (ramo NARRATIVE).

- **Router additivo (deterministico-first):** `router_node` deriva un `intent`
  (`STRUCTURED | VISUAL | NARRATIVE`) da `classify_query` (che gia' da'
  `code/visual/natural/mixed` + `code_token`) + whitelist di keyword prezzo/quantita'.
  Niente LLM per instradare (cheap, auditabile).
    - `code`/prezzo/quantita' -> STRUCTURED -> tool SQL su `estrazione.righe_listino` (1.2).
    - `visual` -> VISUAL -> tool PixelRAG (1.1).
    - `natural`/`mixed` -> NARRATIVE -> RAG testuale esistente.
- **Convergenza:** tutti i rami finiscono su **guardrail (P3.1) -> finalize (P3.2)
  invariati** = Defense in Depth (il guardrail codici e il "non lo so" valgono anche
  sulle risposte SQL renderizzate dall'LLM).
- **Text-to-Parameters (NON Text-to-SQL):** l'LLM non genera mai SQL eseguibile. Si
  estraggono solo PARAMETRI (codice_normalizzato + campo da whitelist) e si esegue
  una query template parametrizzata.
- **Ports & Adapters:** `LanguageModel` (FakeLLM nei test; `MLXModel` reale
  Gemma4/Nemotron differito), `sql_query` e `visual_search` iniettati in `Deps`.
  L'intero grafo testabile senza MLX/Qdrant/Postgres.

## 2. Stato: Passo 1 COMPLETATO e COMMITTATO

- `src/graph/llm.py` — Port `LanguageModel` (`generate(system, user) -> str`) +
  `FakeLLM` (modalita': response fissa / handler dinamico / error per il fail-closed;
  registra `calls`). `tests/test_graph_llm.py` — 7 test verdi.
- Commit: `868e529 feat(fase1.3): port LanguageModel + FakeLLM`.
- Spec committata: `6e3e2fe docs: spec Cervello Unificatore (Fase 1.3)`.

## 3. PRIMO COMPITO della nuova sessione — Passo 2: `sql_tool.py`

Implementa `src/graph/tools/sql_tool.py`: **Text-to-Parameters + query template
parametrizzata + `FakeSqlExecutor`**. Poi mostra il codice all'utente PRIMA di
committare (workflow: review-prima-di-ogni-commit).

**Requisiti di sicurezza NON NEGOZIABILI (l'utente li ha ribaditi esplicitamente):**
- La query si costruisce in modo RIGOROSO con `psycopg.sql`:
    - **colonne** selezionate SOLO da una **whitelist chiusa** (es. `prezzo`,
      `descrizione`), composte con **`psycopg.sql.Identifier`** — mai dal valore
      grezzo dell'utente/LLM.
    - il **`codice_normalizzato`** passa SOLO come parametro **`%s`** (placeholder
      psycopg), calcolato da noi con `codes.canonical`.
- **ASSOLUTAMENTE VIETATE le interpolazioni di stringa** (niente f-string,
  `%`-format o `+` per costruire SQL). Nessun SQL arbitrario dal modello.
- Tabella interrogata: SOLO `estrazione.righe_listino` (Semantic Layer). Prevedere
  ruolo DB read-only come difesa in profondita'.
- **Fail-closed:** codice non presente in tabella -> "non lo so", mai un valore
  inventato.
- **PRETENDI test anti-injection:** input malevoli (es. `codice = "0; DROP TABLE
  righe_listino; --"`, o un `campo` fuori whitelist) devono essere neutralizzati/
  rifiutati, non eseguiti. Unit test col `FakeSqlExecutor` (DB-free) + un test
  `needs_pg` sulla query reale (pattern `tests/conftest.py`, schema usa-e-getta
  come `test_extraction_load.py`).

### Riferimenti utili gia' nel repo
- `src/ingestion/codes.py::canonical` — normalizzazione codici (riusare, non reinventare).
- `src/ingestion/extraction/load.py` — pattern `psycopg.sql.Identifier` + schema
  parametrizzabile per i test (copiare lo stile).
- `src/common/roles.py` — `Role` (StrEnum) per il filtro RBAC.
- Schema Fase 1.2: `estrazione.righe_listino(doc_stem, page, codice_normalizzato,
  codice_originale, descrizione, prezzo, updated_at)`.

## 4. Roadmap residua Fase 1.3 (dopo il Passo 2)
3. Router `intent` (estensione deterministica) + test.
4. `sql_node` + render(LLM) + branch STRUCTURED nel grafo + test (FakeLLM).
5. `visual_tool` + `visual_node` + branch VISUAL + test (FakeVisualIndex).
6. e2e `answer()` sui tre intent + guardrail sulle risposte SQL.
Adapter reale `MLXModel` differito a server acceso (come DeepSeek 1.2 / Qwen3-VL 1.1).
