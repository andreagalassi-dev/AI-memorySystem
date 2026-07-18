# Kickoff — Fase 2: Merge Relazionale (Catalogo × Listino) + Dashboard Scarti

> Piano ufficiale: `docs/piano_implementazione/02_fase2_merge.md` (leggerlo per primo).
> Branch di lavoro: `feature/fase1-pipeline` (Fase 1.3 chiusa qui, NON mergiata).
> Memorie utili: `fase1.3-cervello-unificatore`, `fase1.2-estrazione-massiva`, `fase1.1-pixelrag`.

## 1. Da dove partiamo (stato VERIFICATO sul DB, non presunto)

Fase 1.3 (Cervello Unificatore) **chiusa al 100%**: orchestratore tri-intent su adapter
reali. Per la Fase 2 conta lo stato dei dati persistiti — verificato con query su
`aike_postgres` il 2026-07-10:

- **DB Prezzi (relazionale, ESISTE):** `estrazione.righe_listino(doc_stem, page,
  codice_normalizzato, codice_originale, descrizione, prezzo, updated_at)`. Popolato dal
  binario di estrazione 1.2 (GLM-OCR → doppia barriera Fail-Closed). **Ha già la
  chiave `codice_normalizzato`** (da `src/ingestion/codes.py::canonical`, `[0-9A-Z]`).
- **Scarti estrazione (ESISTE):** `estrazione.scarti_estrazione(id, doc_stem, page,
  codice_originale, codice_normalizzato_tentato, payload_raw jsonb, motivo, created_at)`.
  È lo scarto di *estrazione* 1.2 — concettualmente adiacente ma DISTINTO dagli "scarti di
  *merge*" della Fase 2 (§2.2). Valutare se estendere o affiancare una nuova tabella.
- **Lato Prodotti/Catalogo visivo (NON relazionale):** vive in **Qdrant**
  `aike_pages_visual` (1035 punti, page-level, ColQwen2 multivettore). Payload:
  `doc, page, cat, ruoli_consentiti, version_status, doc_version, render`.
  **NON contiene `codice_normalizzato`.**

## 2. ⚠️ Prima decisione di design (da sciogliere PRIMA di scrivere il JOIN)

Il piano §2.1 parla di "JOIN su codice normalizzato" tra *DB Prodotti* e *DB Prezzi*, ma
**oggi non esiste una tabella relazionale di prodotti con codice per-prodotto**: i prezzi
hanno il codice, il catalogo visivo è a livello di PAGINA su Qdrant (nessun codice nel
payload). Quindi il primo compito è definire cos'è concretamente il "DB Prodotti":

- **Ipotesi A** — estrarre i codici dai cataloghi visivi in una nuova tabella relazionale
  (es. `estrazione.prodotti_catalogo(codice_normalizzato, doc_stem, page, ...)`), poi
  `JOIN righe_listino USING (codice_normalizzato)`. Richiede un estrattore di codici dagli
  esplosi (nuovo lavoro di ingestion, possibile gate di qualità come P1).
- **Ipotesi B** — il "prodotto" è la riga di `righe_listino` stessa; il JOIN "visivo" è
  un LINK deterministico riga↔pagina Qdrant tramite `doc_stem` (+ eventuale codice sulla
  pagina). Il codice normalizzato resta chiave verso i prezzi; il collegamento all'immagine
  è per documento/pagina.

Non decidere da solo: **proporre le ipotesi all'utente** e far scegliere. Questa scelta
determina se la Fase 2 include o no un nuovo stadio di ingestion.

## 3. Cosa dobbiamo costruire (dal piano §2.1–2.2)

1. **Funzione di Merge / Cucitura Relazionale (SQL):** JOIN nativo Postgres su
   `codice_normalizzato` tra il binario strutturato (prezzi) e quello visivo (una volta
   definito il lato prodotti, §2). Il codice normalizzato è la **chiave esterna
   universale** riga-prezzo ↔ immagine/tile su Qdrant. **Niente GraphDB.**
2. **Tabella "Scarti da verificare" (merge):** righe incerte/orfane separate, con:
   codice originale + normalizzato tentato; pagina + PDF sorgente (link verifica visiva);
   motivo (codice non trovato nel catalogo, match ambiguo, prezzo mancante/anomalo).
3. **Dashboard Operativa (Streamlit, riusa P5/`p5-ui`):** revisione manuale scarti;
   azione "Forza Associazione" (1 click) per risolvere i casi limite.

## 4. Vincoli inossidabili (NON negoziabili)

- **PostgreSQL = Single Source of Truth.** La cucitura è SQL JOIN nativo. Qdrant resta la
  proiezione di ricerca; l'autorità del dato è Postgres (come da P4).
- **Niente GraphDB / GraphRAG.** Il codice normalizzato è la chiave universale: azzera
  latenze ed evita overhead. (piano §2.1)
- **Fail-Closed assoluto** (CLAUDE.md): "giusto, oppure «non lo so» — mai sbagliato in
  silenzio". Nel merge: una riga incerta/orfana **non viene mai associata a indovinare**
  → finisce negli scarti per revisione umana. Ogni associazione automatica deve avere
  evidenza; nel dubbio, scarto.
- **RBAC obbligatorio** su ogni dato esposto (ruolo fail-closed, mai default aperto).
- **Ports & Adapters + TDD stretto** (Red→Green→Refactor), un Passo alla volta con OK
  esplicito dell'utente; schema Postgres **usa-e-getta** per i test (`tests/conftest.py`,
  marker `needs_pg`); niente interpolazione di stringa nell'SQL (`psycopg.sql.Identifier`).
- **Un Punto della roadmap alla volta**, commit piccoli, review prima di ogni commit.

## 5. Come lavora l'utente
Design/brainstorm → mostra il piano → attende OK esplicito → TDD → mostra il codice →
attende OK → commit col messaggio concordato. Ignorare i 3 rossi ambientali noti
(Qdrant-corpus: `test_rbac` ×2, `test_search_filter`), indipendenti dal codice.
