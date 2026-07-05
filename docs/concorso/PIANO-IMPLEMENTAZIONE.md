# Piano di implementazione — micro-step per agenti AI

> **Come leggere questo documento.** Ogni voce è un **micro-step autonomo**, pensato
> per essere eseguito da un agente AI che **non conosce il resto del progetto**. Ogni
> step dice: perché esiste, quali file toccare (percorso esatto), cosa fare passo per
> passo, come verificare il risultato con un comando, e quando considerarlo finito.
> Gli step vanno eseguiti **in ordine**. Un agente esegue **un solo step per volta**,
> lo verifica, fa un commit, e si ferma.
>
> **Fonti di questo piano:** la revisione `docs/concorso/REVISIONE-STATO-E-CORREZIONI.md`
> (problemi attuali) e la presentazione prodotto `docs/concorso/Presentazione
> Prodotto_ AI Knowledge Engine.pdf` (dove deve arrivare l'app: estrazione massiva
> dati → guardrail → RBAC/visiva → "Agente Operativo" con preventivi, bot clienti,
> ERP e Distinte Base/BOM).

---

## REGOLE PER OGNI AGENTE (leggere PRIMA di qualsiasi step)

Queste regole valgono per **tutti** gli step. Non saltarle.

1. **Regola d'oro del progetto:** il sistema deve essere **giusto, oppure dire "non
   lo so" — mai sbagliato in silenzio.** Non introdurre mai codice che "indovina".
   Ogni risposta cita la fonte (documento/pagina). I codici ricambio si mostrano solo
   se **esistono davvero** nel dato recuperato.

2. **Ambiente.** Il progetto usa Python con `uv`. Prima di ogni cosa, dalla cartella
   radice del repo (`AI-memorySystem/`):
   ```bash
   source .venv/bin/activate    # attiva l'ambiente già presente
   ```
   Se un comando dice "non trovato", esegui `uv sync` una volta e riprova.

3. **Servizi.** Alcuni step hanno bisogno di Postgres e Qdrant attivi. Si avviano con:
   ```bash
   docker compose up -d postgres qdrant
   ```
   Gli step marcati **[SERVE DB]** richiedono questi due container attivi.

4. **Test = verità.** Dopo ogni modifica esegui **esattamente** il comando di verifica
   indicato nello step. Non dichiarare "fatto" senza aver visto l'output verde.
   Comando generico per l'intera suite:
   ```bash
   python -m pytest tests/ -q
   ```

5. **Un test già rosso non è colpa tua** se lo era anche prima. I test
   `tests/test_rbac.py` e `tests/test_search_filter.py` falliscono quando Qdrant non
   ha dati indicizzati: è normale in un ambiente vuoto. Ignorali a meno che lo step
   non ti chieda esplicitamente di lavorarci.

6. **Commit.** A fine step, un commit piccolo con messaggio chiaro. Formato:
   `fix(area): cosa` oppure `feat(area): cosa`. NON committare file dentro `corpus/`,
   `data/`, `output/`, `.env`.

7. **Non allargare lo scope.** Fai **solo** ciò che lo step chiede. Se trovi altri
   problemi, annotali in fondo al file `docs/concorso/PIANO-IMPLEMENTAZIONE.md` sotto
   "Note emerse durante l'esecuzione", ma non risolverli.

---

# FASE A — Rimettere il sistema in verde (riparazioni)

> Obiettivo della fase: la suite di test non deve avere fallimenti causati dal codice.
> Sono correzioni piccole e a basso rischio. Falle **in ordine**.

## [A1] Aggiornare il test del versioning ai nomi nuovi

- **Contesto:** un refactor ha rinominato una funzione del grafo, ma un vecchio test
  importa ancora il nome vecchio e va in errore di import (quindi non parte proprio).
  Nomi cambiati: `fallback_node` → `fallback_superseded_node`; `fallback_done` →
  `fallback_superseded_done`.
- **File da modificare:** `tests/test_graph_versioning.py`
- **Cosa fare (passo-passo):**
  1. Apri il file. Cerca ogni occorrenza di `fallback_node` e sostituiscila con
     `fallback_superseded_node` (righe circa 16 e 61).
  2. Cerca ogni occorrenza di `fallback_done` e sostituiscila con
     `fallback_superseded_done` (righe circa 65 e 71).
  3. Non cambiare la logica del test, solo i nomi.
- **Come verificare:**
  ```bash
  python -m pytest tests/test_graph_versioning.py -q
  ```
  Atteso: tutti i test del file **passano** (niente più ImportError).
- **Fatto quando:** il file di test si carica ed è verde.
- **NON fare:** non toccare `src/graph/pipeline.py`.

## [A2] Correggere lo scanner del Watchdog sul confronto delle date

- **Contesto:** in `src/watchdog/scanner.py`, alla riga ~103, il codice chiama
  `.timestamp()` sul valore `file_mtime` letto dal database, dando per scontato che
  sia un oggetto `datetime`. Ma può arrivare anche come numero (float). Se è già un
  numero, `.timestamp()` non esiste e il programma va in crash.
- **File da modificare:** `src/watchdog/scanner.py`
- **Cosa fare (passo-passo):**
  1. Individua la riga: `db_mtime = t["file_mtime"].timestamp()`.
  2. Sostituiscila con una gestione che accetti sia `datetime` sia numero. Esempio:
     ```python
     raw_mtime = t["file_mtime"]
     if hasattr(raw_mtime, "timestamp"):
         db_mtime = raw_mtime.timestamp()   # datetime dal DB
     else:
         db_mtime = float(raw_mtime)        # già epoch (test o valori grezzi)
     ```
  3. Lascia invariata la logica successiva (la tolleranza di 1 secondo).
- **Come verificare:**
  ```bash
  python -m pytest tests/test_watchdog.py -q
  ```
  Atteso: `test_poller_idempotency` **passa** (più gli altri del file).
- **Fatto quando:** i test in `tests/test_watchdog.py` sono verdi.
- **NON fare:** non cambiare la logica di `size_changed` né la struttura di `diff_files`.

## [A3] Far comparire davvero l'avviso "documento ELIMINATO"

- **Contesto:** in `src/graph/pipeline.py`, la funzione `reconcile()` esce subito con
  il messaggio standard "non lo so" quando il livello è `BASSA` (riga ~248). Ma il
  blocco che aggiunge l'avviso *"risposta basata su documento ELIMINATO"* è più in
  basso (riga ~263) e **non viene mai raggiunto** in quel caso. Risultato: chi riceve
  una risposta da un documento eliminato non viene avvisato. Va deciso e reso coerente
  con il test `tests/test_deleted_cascade.py::test_warning_deleted_reconcile`.
- **File da modificare:** `src/graph/pipeline.py` (funzione `reconcile`)
- **Cosa fare (passo-passo):**
  1. Leggi la funzione `reconcile` per intero.
  2. Sposta la gestione dei warning **prima** del return anticipato su `BASSA`. In
     pratica: se `state.get("deleted_warning")` è vero, la funzione deve restituire un
     `final_answer` che **contiene la parola `ELIMINATO`** e `final_level` = `"BASSA"`,
     **anche** quando la risposta di base è un rifiuto. La logica corretta è:
     - se `deleted_warning`: costruisci il messaggio di rifiuto/o la risposta, poi
       **appendi** il testo di avviso "ELIMINATO", imposta livello `BASSA`, e ritorna.
  3. Mantieni invariato il comportamento per `obsolete_warning` (sostituito) e per gli
     altri rami (`access_error`, `system_error`, guardrail BLOCK/VERIFY).
- **Come verificare:**
  ```bash
  python -m pytest tests/test_deleted_cascade.py -q
  ```
  Atteso: sia `test_deleted_cascade_routing` sia `test_warning_deleted_reconcile`
  **passano** (`ELIMINATO` presente nel testo, livello `BASSA`).
- **Fatto quando:** entrambi i test del file sono verdi e la suite generale non
  peggiora.
- **NON fare:** non toccare il routing del grafo (`_after_confidence`), solo `reconcile`.

## [A4] [SERVE DB] Correggere la propagazione del soft-delete a Qdrant

- **Contesto:** quando un file viene cancellato dalla cartella, `src/watchdog/soft_delete.py`
  aggiorna Postgres a `deleted`, ma la chiamata che dovrebbe aggiornare Qdrant è
  **sbagliata**: chiama `sync_collection(records)` con un solo argomento, mentre la
  firma vera è `sync_collection(qc, coll, export)` (3 argomenti: client Qdrant, nome
  collezione, dizionario export). L'errore viene nascosto da un `try/except` che si
  limita a loggare. Conseguenza grave: il documento eliminato resta `current` in
  Qdrant → continua a comparire nelle risposte, e la "cascata eliminato" non trova mai
  nulla.
- **File da modificare:** `src/watchdog/soft_delete.py`
- **Riferimenti (già esistenti, non reinventare):**
  - firma corretta: `src/registry/sync.py` → `sync_collection(qc, coll, export)`.
  - nome collezione: `from src.ingestion.embedding import COLLECTION`.
  - client: `from src.backend.clients.qdrant_client import get_qdrant_client`.
  - export SSOT: `from src.registry.registry import export_backfill` (ritorna un dict
    `stem -> BackfillRecord`; `sync_collection` vuole **tutto** il dict, non un
    singolo record).
- **Cosa fare (passo-passo):**
  1. Nel blocco che oggi fa `sync_collection(records)`, sostituisci con:
     ```python
     qc = get_qdrant_client()
     export = export_backfill(conn)
     sync_collection(qc, COLLECTION, export)
     ```
     (aggiungi gli import in cima al file se mancano).
  2. **Non** nascondere più l'errore in modo muto: se `sync_collection` fallisce,
     l'eccezione deve propagarsi (o essere rilanciata dopo il log) così il worker la
     registra e mette il file in stato `failed`. Togli il `try/except` che ingoia, o
     trasformalo in `logger.error(...); raise`.
  3. Verifica che `conn` passato qui sia lo stesso tipo di connessione usato in
     `src/registry/registry.py` (vedi step A5: driver unico). Se A5 non è ancora fatto,
     annota la dipendenza ma completa comunque la correzione della firma.
- **Come verificare:**
  ```bash
  python -c "import ast,sys; ast.parse(open('src/watchdog/soft_delete.py').read()); print('sintassi ok')"
  python -m pytest tests/ -q -k "cascade or watchdog or version"
  ```
  Atteso: nessun nuovo fallimento; la chiamata usa 3 argomenti.
- **Fatto quando:** `soft_delete.py` chiama `sync_collection` con la firma giusta e
  l'errore non è più silenzioso.
- **NON fare:** non modificare `src/registry/sync.py`.

## [A5] Unificare il driver di database del Watchdog (psycopg unico)

- **Contesto:** il Watchdog mescola due librerie database. `src/watchdog/daemon.py` usa
  `psycopg2` e passa un **cursore**; ma `scanner.py` e `worker.py` sono scritti come se
  ricevessero una **connessione psycopg3** (fanno `conn.execute(...).fetchall()` e
  `conn.commit()`, che su un cursore psycopg2 non esistono/non funzionano). Il resto del
  progetto (`src/registry/registry.py`) usa `psycopg` (versione 3). Va usato **solo
  psycopg 3 ovunque**.
- **File da modificare:** `src/watchdog/daemon.py`, e verifica di `scanner.py` /
  `worker.py`.
- **Cosa fare (passo-passo):**
  1. In `daemon.py` sostituisci `import psycopg2` con `import psycopg` e
     `psycopg2.connect(...)` con `psycopg.connect(...)`.
  2. In psycopg3 la **connessione** ha direttamente `.execute()` e `.commit()`. Quindi
     **non** creare un cursore separato: passa la **connessione** (non `conn.cursor()`)
     alle funzioni `diff_files(...)` e `process_diffs(...)`. Cambia:
     ```python
     with psycopg.connect(self.dsn) as conn:
         ...
         diffs = diff_files(conn, current_files, self.corpus_path)
         ...
         process_diffs(conn, diffs, self.corpus_path, self.default_role)
     ```
     (togli il `with conn.cursor() as cur:` e usa `conn` ovunque c'era `cur`).
  3. Controlla che `scanner.py` e `worker.py` usino coerentemente `conn.execute(...)` e
     `conn.commit()` (già lo fanno: in psycopg3 sono corretti).
  4. Aggiorna la stringa DSN di default in `daemon.py` (`main()`) perché sia coerente
     con `docker-compose.yml`:
     `postgresql://aike:aike_dev@postgres:5432/aike_registry`.
- **Come verificare:**
  ```bash
  python -c "import ast; [ast.parse(open(f).read()) for f in ['src/watchdog/daemon.py','src/watchdog/worker.py','src/watchdog/scanner.py']]; print('sintassi ok')"
  python -m pytest tests/test_watchdog.py -q
  grep -n "psycopg2" src/watchdog/*.py    # atteso: nessun risultato
  ```
- **Fatto quando:** non resta nessun `psycopg2` nel modulo watchdog e i test passano.
- **NON fare:** non introdurre pool di connessioni ora; una connessione per ciclo va bene.

## [A6] Applicare il filtro RBAC anche al recupero dei "figli" (Hop 2)

- **Contesto:** la ricerca a due passi (P5) recupera i componenti figli di un
  "assembly/kit" con `client.scroll(...)` in `src/retrieval/search.py`, funzione
  `apply_hop2`. Quel filtro guarda **solo** `parent_id` e **non** il ruolo utente.
  Rischio: un ruolo (es. commerciale) potrebbe ricevere figli riservati a un altro
  ruolo (es. tecnico). L'RBAC (regola P4.5) dice che il filtro ruolo è **obbligatorio**.
- **File da modificare:** `src/retrieval/search.py` (funzione `apply_hop2` e la sua
  chiamante `search_unified`).
- **Cosa fare (passo-passo):**
  1. Guarda come il resto del retrieval applica il filtro ruolo (cerca nel file la
     chiave payload `ruoli_consentiti` e la costruzione di `models.Filter`). Riusa
     **esattamente** quel meccanismo.
  2. Aggiungi un parametro `role` a `apply_hop2(client, hits, role)` e a
     `search_unified(client, query, limit, role)`.
  3. Nel `models.Filter` della `scroll` di Hop 2, aggiungi una condizione che il campo
     `ruoli_consentiti` contenga il `role` corrente, in `AND` con la condizione su
     `parent_id`.
  4. Propaga il `role` da chi chiama `search_unified` (cerca i chiamanti con
     `grep -rn "search_unified" src/`).
- **Come verificare:**
  ```bash
  python -m pytest tests/ -q -k "rbac or search or hop or retriev"
  grep -n "ruoli_consentiti" src/retrieval/search.py   # atteso: presente in apply_hop2
  ```
  Nota: i test RBAC che richiedono dati potrebbero restare rossi in ambiente vuoto
  (vedi Regola 5). L'importante è che il codice del filtro sia presente e la sintassi ok.
- **Fatto quando:** `apply_hop2` costruisce un filtro che include il ruolo; nessun test
  prima verde diventa rosso.
- **NON fare:** non cambiare l'algoritmo di raggruppamento (`group_by_code`).

## [A7] Verifica finale di Fase A

- **Contesto:** cancello di uscita della fase.
- **Cosa fare:** esegui l'intera suite e confronta con la baseline.
  ```bash
  python -m pytest tests/ -q 2>&1 | tail -15
  ```
- **Fatto quando:** gli **unici** fallimenti residui sono quelli ambientali già noti
  (`test_rbac`, `test_search_filter` per Qdrant vuoto). Nessun fallimento nei file
  `test_graph_versioning`, `test_watchdog`, `test_deleted_cascade`.
- **Commit:** `chore(fase-a): suite in verde dopo riparazioni watchdog/cascade`.

---

# FASE B — Auto-ingestion reale ("carica qualsiasi PDF e cerca")

> Obiettivo: oggi il Watchdog rileva i file ma **non li ingerisce davvero**
> (`src/watchdog/pipeline.py` → `ingest_file` è un segnaposto che scrive solo un log).
> Inoltre l'ingestion attuale (`scripts/ingest_chunks.py`) **ricostruisce da zero
> l'intera collezione** ogni volta: non è adatta ad aggiungere un solo documento.
> Questa fase costruisce un percorso di ingestion **per singolo documento**.

## [B1] Studiare e documentare la pipeline di ingestion esistente (nessun codice)

- **Contesto:** prima di automatizzare, l'agente deve capire i passaggi manuali attuali.
- **File da leggere (solo lettura):** `scripts/chunk_tables.py`, `scripts/ingest_chunks.py`,
  `scripts/normalize_codes_docling.py`, `scripts/backfill_qdrant.py`,
  `src/ingestion/embedding.py`, `src/ingestion/chunking.py`.
- **Cosa fare:** scrivi un file nuovo `docs/ingestion-pipeline-attuale.md` che elenchi,
  in ordine, i comandi che oggi si eseguono a mano per portare un PDF dentro il sistema
  (dal file grezzo fino ai vettori in Qdrant e alla riga in Postgres). Per ogni passo
  indica: script, input, output, e se ricostruisce tutto o lavora sul singolo file.
- **Come verificare:** il file esiste e descrive almeno: (1) estrazione/OCR, (2)
  chunking tabelle, (3) normalizzazione codici, (4) embedding+upsert Qdrant, (5)
  registrazione versione/ruoli in Postgres.
- **Fatto quando:** `docs/ingestion-pipeline-attuale.md` è completo e verificabile.
- **NON fare:** nessuna modifica al codice in questo step.

## [B2] Creare una funzione di ingestion per SINGOLO documento

- **Contesto:** serve una funzione riutilizzabile che, dato **un** file, esegua tutti i
  passaggi trovati in B1, ma **solo per quel file**, senza cancellare gli altri.
- **File da creare:** `src/ingestion/ingest_one.py`
- **Cosa fare (passo-passo):**
  1. Definisci `def ingest_document(filepath: str, *, doc_stem: str, role: str, conn,
     is_update: bool = False) -> dict:` che orchestra i passaggi di B1 per **quel solo
     file**: estrazione → chunking (con `parent_id`/`is_assembly` come già fa
     `src/ingestion/chunking.py`) → normalizzazione codici via profilo brand →
     embedding → **upsert** (non drop!) dei punti in Qdrant con `payload` che includa
     `doc_stem`, `ruoli_consentiti`, `version_status='current'`, `page`, `code`,
     `parent_id`, `is_assembly` → registrazione in Postgres (`register_document`).
  2. Usa gli entry-point già esistenti (importa da `src/ingestion/...`), **non**
     copiare codice: se un passo esiste solo come script CLI, estrai la funzione
     riutilizzabile o chiama lo script via `subprocess` come soluzione-ponte (annota
     che è un ponte da migliorare).
  3. Restituisci un riepilogo: `{"doc_stem":..., "n_chunks":..., "n_points":...,
     "profile_used":..., "profile_warning": bool}`.
- **Come verificare:** [SERVE DB]
  ```bash
  python -c "from src.ingestion.ingest_one import ingest_document; print('import ok')"
  ```
  e un test unitario nuovo `tests/test_ingest_one.py` che, con Qdrant/Postgres attivi e
  un PDF piccolo di prova (usa uno già presente in `corpus/`), verifichi che dopo
  `ingest_document` la collezione contiene punti con quel `doc_stem` e Postgres ha la
  riga versione.
- **Fatto quando:** la funzione ingerisce un singolo documento in modo idempotente
  (rilanciarla sullo stesso file non crea duplicati — vedi B3).
- **NON fare:** non usare `ingest_chunks.py` così com'è (fa drop+create globale).

## [B3] Pulizia dei chunk vecchi prima del re-ingest (niente duplicati)

- **Contesto:** quando un catalogo viene **modificato**, i chunk della versione
  precedente devono sparire da Qdrant, altrimenti il sistema risponde con dati vecchi.
  Oggi `src/watchdog/worker.py` ha un `TODO: Clean up partial chunks`.
- **File da modificare:** `src/ingestion/ingest_one.py` (o una funzione dedicata
  `delete_document_points(qc, coll, doc_stem)` in `src/ingestion/`).
- **Cosa fare (passo-passo):**
  1. Prima di fare l'upsert in caso di `is_update=True` (o sempre, per idempotenza),
     elimina da Qdrant tutti i punti con `payload.doc_stem == doc_stem` usando
     `qc.delete(collection, points_selector=Filter(...))`.
  2. Solo dopo la cancellazione, esegui l'upsert dei nuovi punti.
- **Come verificare:** [SERVE DB] estendi `tests/test_ingest_one.py`: ingerisci lo
  stesso `doc_stem` due volte con contenuto leggermente diverso; verifica che il numero
  di punti sia quello dell'ultima ingestione, non la somma.
- **Fatto quando:** re-ingerire un documento non lascia punti orfani della versione
  precedente.
- **NON fare:** non cancellare punti di **altri** `doc_stem`.

## [B4] Collegare il Watchdog alla funzione di ingestion reale

- **Contesto:** ora che `ingest_document` esiste (B2/B3), il segnaposto va sostituito.
- **File da modificare:** `src/watchdog/pipeline.py` (`ingest_file`), e verifica di
  `src/watchdog/worker.py`.
- **Cosa fare (passo-passo):**
  1. In `ingest_file(filepath, role, is_update=False)`: calcola `doc_stem` con la
     funzione `filename_to_stem` già presente nel file; risolvi il profilo brand con
     `src/watchdog/profile.py` → `resolve_brand_profile(filename, conn)`; poi chiama
     `src.ingestion.ingest_one.ingest_document(...)` passando `conn`.
  2. In `worker.py`, dopo l'ingestione riuscita, se `resolve_brand_profile` ha
     restituito `is_default=True`, scrivi `profile_warning=true` nella riga
     `tracked_files` di quel file (colonna già presente nello schema).
  3. Gestisci l'eccezione: se l'ingestione fallisce, il file resta `failed` con
     `error_log` (già gestito nel `try/except` di `worker.py`).
- **Come verificare:** [SERVE DB] test di integrazione nuovo che simula un ciclo del
  daemon: metti un PDF piccolo in una cartella temporanea, esegui una scansione +
  `process_diffs`, verifica che il file diventi `active` in `tracked_files` e che
  Qdrant contenga i suoi punti.
- **Fatto quando:** trascinare un PDF nella cartella monitorata lo rende ricercabile
  senza comandi manuali.
- **NON fare:** non rimuovere il log informativo esistente.

## [B5] [SERVE DB] Prova end-to-end del ciclo completo del Watchdog

- **Contesto:** cancello di uscita di Fase B. Deve funzionare il ciclo:
  aggiungi → cerca → modifica → cerca → elimina → l'avviso "ELIMINATO" compare.
- **Cosa fare (passo-passo):**
  1. Avvia i servizi: `docker compose up -d postgres qdrant`.
  2. Applica lo schema/migrazioni (esegui l'inizializzazione schema del registry e la
     migrazione indici P5: `python scripts/migrate_p5_indices.py`).
  3. Copia un PDF piccolo (es. da `corpus/`) in una cartella di prova monitorata.
  4. Esegui un ciclo del daemon (o le funzioni scanner→worker a mano).
  5. Interroga il sistema con una query che colpisce quel documento: deve rispondere
     citando la fonte.
  6. Elimina il file dalla cartella, riesegui un ciclo: la successiva risposta basata
     su quel documento deve contenere l'avviso **ELIMINATO** (grazie ad A3 + A4).
- **Come verificare:** scrivi i passaggi e gli output osservati in un file
  `docs/watchdog-e2e.md`. Deve mostrare: file diventato `active`, ricerca positiva,
  poi `deleted` con avviso.
- **Fatto quando:** il ciclo completo è dimostrato con evidenze scritte.
- **Commit:** `feat(fase-b): auto-ingestion per-documento end-to-end`.

---

# FASE C — Robustezza multi-brand ("qualunque case madre, non solo Mamoli")

> Obiettivo: oggi i codici ricambio vengono normalizzati bene solo per i brand che
> hanno un "profilo". Un catalogo nuovo senza profilo passa "grezzo" (codici non
> normalizzati). Per il tuo scenario (molti listini di case madri diverse) serve
> rendere l'aggiunta di un brand semplice e sicura.

## [C1] Report di copertura dei profili brand (nessun codice)

- **Contesto:** capire quanti/quali brand hanno un profilo e quanti passano in "default".
- **File da leggere:** `src/registry/ingestion_profiles.py`, `src/ingestion/brands.py`,
  `scripts/seed_ingestion_profiles.py`, e le tabelle profili in Postgres.
- **Cosa fare:** crea `docs/profili-brand-copertura.md` con: elenco profili presenti,
  campi che ogni profilo definisce (regex codice, pattern assembly, ecc.), e la
  procedura attuale per aggiungerne uno.
- **Fatto quando:** il documento elenca chiaramente cosa serve per definire un profilo.
- **NON fare:** nessuna modifica al codice.

## [C2] Comando guidato per aggiungere un profilo brand

- **Contesto:** aggiungere un brand deve essere un'operazione singola e ripetibile, non
  la modifica di file sparsi.
- **File da creare:** `scripts/add_brand_profile.py`
- **Cosa fare (passo-passo):**
  1. Uno script CLI che riceve i parametri di un profilo (nome brand, glob del nome
     file per l'auto-riconoscimento, regex del codice ricambio, eventuale pattern
     assembly) e li **inserisce nelle tabelle profili in Postgres** in modo idempotente
     (riusa le funzioni di `src/registry/ingestion_profiles.py`; guarda come fa
     `scripts/seed_ingestion_profiles.py`).
  2. Validazione: prima di salvare, testa la regex del codice su 2-3 codici di esempio
     forniti dall'utente e stampa i match, così l'utente vede se la regex funziona.
- **Come verificare:** [SERVE DB]
  ```bash
  python scripts/add_brand_profile.py --help
  ```
  e un test che inserisce un profilo fittizio e lo rilegge da `load_profiles`.
- **Fatto quando:** un profilo nuovo si aggiunge con un solo comando e si ritrova in DB.
- **NON fare:** non toccare i profili esistenti.

## [C3] Normalizzatore "generico" di sicurezza per brand senza profilo

- **Contesto:** anche senza profilo, meglio un tentativo prudente che un passthrough
  totale — **purché** resti dentro la regola d'oro (mai inventare). Un codice
  normalizzato "a indovinare" non deve mai essere presentato come certo.
- **File da modificare:** il normalizzatore (vedi `scripts/normalize_codes_docling.py` /
  `src/ingestion/brands.py`).
- **Cosa fare (passo-passo):**
  1. Definisci un profilo `generic` che riconosca pattern di codice **molto comuni**
     (es. sequenze alfanumeriche con trattini/lettere+cifre) SENZA assumere un brand.
  2. Quando si usa `generic`, marca i chunk con un flag payload `code_normalized_by =
     "generic"`.
  3. In fase di risposta, un codice che proviene da normalizzazione `generic` non può
     produrre confidenza **ALTA** (cap a MEDIA con "verifica consigliata"), coerente con
     il comportamento dei warning esistenti in `src/retrieval/confidence.py`.
- **Come verificare:** test unitario che, dato un codice normalizzato `generic`, la
  confidenza risultante non sia mai `ALTA`.
- **Fatto quando:** i cataloghi senza profilo restano cercabili ma prudenti.
- **NON fare:** non lasciare che `generic` scavalchi il guardrail deterministico sui
  codici (il codice deve comunque **esistere** nel dato recuperato).

## [C4] Verifica multi-brand con almeno due cataloghi diversi

- **Contesto:** cancello di uscita di Fase C.
- **Cosa fare:** ingerisci due cataloghi di brand diversi (uno con profilo dedicato,
  uno con `generic`), poi interroga entrambi. Misura il recupero **separatamente per
  documento/lingua** (regola SPEC §F.1). Documenta in `docs/multi-brand-eval.md`.
- **Fatto quando:** entrambi i cataloghi rispondono con fonte; il caso `generic` mostra
  confidenza prudente.
- **Commit:** `feat(fase-c): onboarding brand guidato + normalizzatore generico prudente`.

---

# FASE D — Verso l'"Agente Operativo" (visione del PDF)

> Obiettivo: le funzionalità della sezione "Vision & Roadmap" della presentazione.
> Sono grandi: ognuna va spezzata ancora, ma qui sono già in micro-step iniziali.
> **Non iniziare la Fase D senza l'OK esplicito del proprietario del progetto.**

## [D1] Estrazione Distinte Base (BOM) dagli assembly già indicizzati

- **Contesto:** il sistema conosce già `parent_id`/`is_assembly` (P5). La BOM è
  "dato tutti i figli di un assembly, elencali con quantità".
- **File da creare:** `src/retrieval/bom.py` + `scripts/export_bom.py`.
- **Cosa fare (passo-passo):**
  1. Funzione `get_bom(client, kit_code, role) -> list[dict]` che riusa l'Hop 2
     (`apply_hop2`, con filtro ruolo di A6) e restituisce i figli con `code`,
     `description`, `quantity`, `page`, `source`.
  2. Script `export_bom.py --kit <codice>` che stampa/esporta la BOM in CSV/JSON con
     puntatore al documento d'origine (citazione obbligatoria).
  3. Guardrail: ogni codice figlio deve esistere nel dato recuperato; nessun codice
     "dedotto".
- **Come verificare:** [SERVE DB] su un assembly reale del corpus, la BOM elenca i
  figli attesi (usa/estendi `scripts/evaluate_hierarchy.py` con `expected_children`
  reali, non mock).
- **Fatto quando:** una BOM corretta e citata si esporta con un comando.

## [D2] Bozza preventivo da lista di richieste (senza invio, solo bozza)

- **Contesto:** primo passo di "Automazione Preventivi". L'AI trova i codici esatti da
  richieste testuali e compila una **bozza** verificabile dall'umano (mai automatica).
- **File da creare:** `src/agent/quote_draft.py`.
- **Cosa fare (passo-passo):**
  1. Input: lista di richieste in linguaggio naturale (una per riga).
  2. Per ciascuna: usa il retrieval esistente per trovare il codice; se la confidenza
     non è ALTA, marca la riga come "da verificare" invece di indovinare.
  3. Output: tabella `richiesta → codice proposto → descrizione → confidenza → fonte`.
- **Come verificare:** test con 3 richieste note; le righe a bassa confidenza risultano
  "da verificare", non compilate a caso.
- **Fatto quando:** la bozza è prodotta e ogni riga è tracciabile alla fonte.
- **NON fare:** nessun invio automatico, nessun ordine reale. Solo bozza.

## [D3] e oltre — placeholder da dettagliare col proprietario

- **Bot clienti sicuro** (esposizione esterna del motore anti-allucinazione): richiede
  decisioni di sicurezza/perimetro → definire con il proprietario prima di progettare.
- **Integrazione ERP in sola lettura** (sync notturna codici/giacenze/prezzi → Postgres):
  è P7 nella roadmap SPEC; scegliere prima **quale** gestionale.
- **Multi-tenancy + refactor FastAPI/frontend**: P7, dopo aver dimostrato l'accuratezza.

Questi non sono ancora micro-step eseguibili: vanno aperti in un piano dedicato quando
si arriva qui, perché dipendono da decisioni di business (SPEC §H) non ancora prese.

---

## Note emerse durante l'esecuzione
*(Gli agenti annotano qui i problemi trovati fuori scope, senza risolverli.)*
</content>
