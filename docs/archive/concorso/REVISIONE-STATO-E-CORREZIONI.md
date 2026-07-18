# Revisione dello stato del sistema — cosa correggere prima di andare avanti

> **A cosa serve questo file.** Hai chiesto una revisione delle modifiche in corso
> (dai commit dopo `feat(p4.6)` fino a `HEAD`, più le modifiche non ancora
> committate) per capire, in modo semplice: **(1)** quali problemi ci sono da
> sistemare, **(2)** cosa manca perché il sistema funzioni con *qualsiasi* PDF, e
> **(3)** cosa fare una volta sistemato tutto.
>
> **Nota sul "PDF" in `docs/concorso/`:** in quella cartella **non c'è nessun PDF**,
> solo il file `thecnical-debt-analyst.md`. Ho usato quello come fonte per la
> visione futura (debiti tecnici e soluzioni P6/P7). Se il PDF esisteva, non è
> stato salvato nel repo.

---

## In due righe

Il lavoro dopo P4 (i "debiti tecnici" §1–§4 e l'avvio del **Watchdog/auto-ingestion**
P7) è **promettente ma non ancora sano**: ci sono **3 test rotti causati dalle
modifiche** e **alcune parti scritte ma non funzionanti a runtime** (il pezzo che
davvero ingerisce i PDF è ancora un segnaposto). Va messo in verde e completato
prima di procedere.

**Prova oggettiva (test):** `356 passati, 6 falliti, 1 errore`.
- **3 problemi sono colpa delle modifiche recenti** → vanno corretti (sotto).
- **4 falliscono anche senza le modifiche** (`test_rbac`, `test_search_filter`):
  sono ambientali — Qdrant senza dati indicizzati — *non* causati da questo lavoro.
  (Verificato con `git stash`: rossi anche sull'albero pulito.)

---

## PARTE 1 — Problemi da sistemare (dal più al meno urgente)

### 🔴 1. Un refactor a metà ha rotto i test del versioning (P4.4)
- **Cosa succede:** il nodo del grafo `fallback_node` è stato rinominato in
  `fallback_superseded_node` (e `fallback_done` → `fallback_superseded_done`), ma
  il test `tests/test_graph_versioning.py` importa ancora i **nomi vecchi**.
- **Effetto:** il file di test non si carica nemmeno (`ImportError`) → tutta la
  copertura sul fallback "documento sostituito" è spenta.
- **Come si vede:** `ImportError: cannot import name 'fallback_node'`.
- **Fix:** aggiornare `test_graph_versioning.py` ai nuovi nomi (4 riferimenti: righe
  16, 61, 65, 71). 10 minuti.

### 🔴 2. L'avviso "documento ELIMINATO" non compare mai quando serve
- **Cosa succede:** in `src/graph/pipeline.py`, `reconcile()` esce **subito** se il
  livello è `BASSA` (riga 248, ritorna il messaggio standard "non lo so"). Ma il
  blocco che aggiunge l'avviso *"risposta basata su documento ELIMINATO"* sta più
  in basso (riga 263) **e forza proprio il livello a BASSA**. Le due cose si
  contraddicono: nell'unico caso in cui l'avviso dovrebbe apparire, non viene mai
  raggiunto.
- **Effetto:** il nuovo test `test_warning_deleted_reconcile` è **rosso**; a runtime
  un utente che ottiene una risposta da un documento eliminato **non viene
  avvisato** che è eliminato.
- **Fix:** gestire `deleted_warning` (e `obsolete_warning`) **prima** del return su
  `BASSA`, oppure decidere che una risposta "eliminato" resti MEDIA con avviso, non
  BASSA muta. Va deciso il comportamento voluto e reso coerente col test.

### 🔴 3. Lo scanner del Watchdog crasha sul confronto delle date
- **Cosa succede:** `src/watchdog/scanner.py:103` chiama `.timestamp()` assumendo che
  la data del file dal DB sia un `datetime`; ma nel test (e in alcuni casi reali)
  arriva come numero → `AttributeError: 'float' object has no attribute 'timestamp'`.
- **Effetto:** il test `test_poller_idempotency` è **rosso**; la logica "il file è
  cambiato?" è fragile.
- **Fix:** normalizzare il tipo di `file_mtime` (gestire sia `datetime` sia numero)
  prima del confronto.

### 🟠 4. La cancellazione di un file **non arriva a Qdrant** (fallimento silenzioso)
- **Cosa succede:** `src/watchdog/soft_delete.py` chiama `sync_collection(records)`,
  ma la funzione vera ha un'altra firma: `sync_collection(qc, coll, ...)`. La
  chiamata solleva un errore che però viene **inghiottito** da un `try/except` che
  si limita a loggarlo.
- **Effetto (importante):** quando cancelli un PDF dalla cartella, Postgres lo segna
  `deleted`, **ma i suoi pezzi in Qdrant restano `current`**. Quindi:
  1. il documento eliminato **continua a comparire** nelle risposte come se fosse
     valido (il contrario di quello che vuoi);
  2. la "cascata sull'eliminato" appena costruita (che cerca i chunk con
     `version_status='deleted'`) **non troverà mai nulla**, perché quello stato in
     Qdrant non viene mai scritto. SSOT (Postgres) e Qdrant divergono.
- **Fix:** correggere la chiamata a `sync_collection` con la firma giusta e
  **togliere il catch che nasconde l'errore** (o almeno farlo fallire "rumoroso").

### 🟠 5. Il Watchdog mescola due driver di database e non è mai stato eseguito davvero
- **Cosa succede:** `daemon.py` usa `psycopg2` e passa un **cursore**; ma
  `scanner.py`/`worker.py` sono scritti per `psycopg3` (fanno
  `conn.execute(...).fetchall()` e `conn.commit()`, che su un cursore psycopg2
  non funzionano). I test usano un finto `MockConn`, quindi il problema non emerge
  nei test ma **esploderebbe al primo avvio reale**.
- **Effetto:** il demone Watchdog, così com'è, **non gira** contro un Postgres vero.
- **Fix:** scegliere **un solo** driver (coerente col resto del progetto, che usa
  `psycopg`/psycopg3 in `registry.py`) e passare ovunque lo stesso oggetto
  (connessione o cursore) con la stessa semantica. Poi un test di integrazione con
  Postgres vero, non solo mock.

### 🟡 6. Da verificare: l'Hop 2 (figli di un "kit") potrebbe scavalcare l'RBAC
- **Cosa succede:** in `src/retrieval/search.py`, `apply_hop2()` recupera i figli di
  un assembly con `client.scroll(...)` filtrando **solo** su `parent_id`, senza il
  filtro sui ruoli.
- **Rischio:** un utente "commerciale" potrebbe ricevere i componenti figli di un
  assembly "tecnico". Va verificato se il filtro ruoli è applicato altrove; se no,
  è una fuga di dati RBAC (P4.5 dice che il filtro ruolo è **obbligatorio**).
- **Fix:** propagare il ruolo fino all'Hop 2 e aggiungere il filtro
  `ruoli_consentiti` anche sulla `scroll`.

---

## PARTE 2 — Cosa manca perché funzioni con QUALSIASI PDF

Questo è il cuore del tuo obiettivo ("raccoglierò molti listini e cataloghi").
Oggi il sistema è tarato sul corpus attuale; per essere "carica-e-vai" mancano:

### A. 🔴 Il Watchdog **non ingerisce davvero** — è un guscio
- `src/watchdog/pipeline.py` → `ingest_file()` **logga soltanto** e ha un `TODO:
  integrare chiamata reale a Docling/Ingestion`. Rileva i file, aggiorna la tabella
  `tracked_files`, ma **non estrae, non fa chunking, non vettorizza**.
- **Senza questo, l'auto-ingestion non esiste.** È la prima cosa da completare:
  collegare `ingest_file` alla pipeline reale (Docling → `chunk_tables`/chunking →
  embedding → upsert Qdrant → registrazione versione in Postgres).

### B. 🟠 Ogni nuovo brand ha bisogno del suo "profilo" (overfitting Mamoli)
- Buona notizia: i profili di ingestion sono già stati spostati da regex hardcoded
  a tabelle Postgres (debito §1, già su `main`). `src/watchdog/profile.py` risolve
  il profilo dal nome file e **avvisa** se non ne trova uno ("default passthrough").
- Ma "default passthrough" significa **codici ricambio NON normalizzati** → il RAG
  ibrido perde efficacia su quel catalogo.
- **Per "qualsiasi PDF" serve una di queste:** (a) creare un profilo per ciascun
  brand che caricherai (regex codici, pattern assembly, ecc.), oppure (b) un
  normalizzatore generico più robusto che indovini il formato codice senza profilo.
  Realistico: un **flusso guidato** per aggiungere un profilo quando ne carichi uno
  nuovo.

### C. 🟠 Re-ingest di un file modificato **non ripulisce i vecchi pezzi**
- `src/watchdog/worker.py` ha `TODO: Clean up partial chunks if processing_orphan or
  modified`. Se aggiorni un catalogo, i chunk della versione precedente **restano**
  in Qdrant → duplicati e risposte da dati vecchi.
- **Fix:** prima del re-ingest, eliminare/segnare i chunk del documento (già hai la
  logica di versioning P4 da riusare).

### D. 🟡 La metrica del recupero "gerarchico" (kit/assembly) è solo dimostrativa
- `scripts/evaluate_hierarchy.py` calcola `R_hierarchy` su un **eval set finto**
  (`CHILD001`, `CHILD002`). Non c'è ancora misura reale: l'eval set vero
  (`eval_set_v1`) non ha il campo `expected_children`.
- **Fix:** arricchire alcuni casi dell'eval set reale con i figli attesi e misurare
  davvero (separando per lingua, come da SPEC §F.1).

### E. 🟡 Lo stock è ancora finto (debito §6)
- La giacenza è un hash deterministico nella UI, non un dato ERP. Per la produzione
  serve la sync notturna in sola lettura (P7). Non blocca i test, blocca l'uso reale.

---

## PARTE 3 — Cosa fare DOPO aver corretto/completato

Ordine consigliato, coerente con la roadmap `docs/SPEC.md`:

1. **Rimettere in verde la suite.** Correggere i punti 1–5 della Parte 1. *Prova di
   uscita:* `pytest tests/` senza fallimenti attribuibili al codice (restano solo,
   se presenti, i 4 test ambientali che richiedono Qdrant popolato — meglio ancora
   marcarli come `integration` e popolarli).

2. **Completare davvero l'auto-ingestion (Parte 2.A + 2.C).** È ciò che rende il
   sistema "carica-e-vai" per i tuoi listini. Prima `ingest_file` reale, poi pulizia
   chunk sul re-ingest, poi un test di integrazione con Postgres+Qdrant veri (non
   mock).

3. **Chiudere P5 (UI + feedback).** Da memoria progetto resta la **moderazione delle
   correzioni** prima che entrino nel ground truth (issue #13). Serve perché il
   ciclo di feedback non "avveleni" il set di valutazione.

4. **P6 — decisioni non-codice (in parallelo).** Cronometrare le ore reali di
   onboarding sui primi cataloghi (prodotto vs consulenza), decisione deployment,
   mappa concorrenti, modello di prezzo. Sono le domande di SPEC §H.

5. **P7 vero.** Il Watchdog è già P7: una volta solido, si prosegue con sync ERP in
   sola lettura, multi-tenancy con test anti-leak, e refactor Streamlit → FastAPI +
   frontend separato.

---

## Appendice — Prove raccolte

- **Commit rivisti:** da `79adef2` (debito §1) a `ce120d4` (Two-Step Retrieval P5),
  più le modifiche non committate (`docker-compose.yml`, `src/graph/pipeline.py`,
  `src/registry/{registry.py,schema.sql}`, `src/retrieval/confidence.py`) e i file
  non tracciati (`src/watchdog/*`, `scripts/evaluate_hierarchy.py`,
  `scripts/migrate_p5_indices.py`, `tests/test_watchdog.py`,
  `tests/test_deleted_cascade.py`, `docker/watchdog.Dockerfile`).
- **Esito test (osservato):** `356 passed, 6 failed, 1 error`.
  - Rotti dalle modifiche: `test_graph_versioning` (import), `test_deleted_cascade::
    test_warning_deleted_reconcile`, `test_watchdog::test_poller_idempotency`.
  - Ambientali/pre-esistenti (rossi anche con `git stash`): 2×`test_rbac`,
    2×`test_search_filter` — Qdrant senza dati.
- **Nota:** i debiti §1–§4 risultano **già mergiati su `main`** (commit di merge
  presenti nel log), a differenza di quanto indicato in alcune note di memoria più
  vecchie.
</content>
</invoke>
