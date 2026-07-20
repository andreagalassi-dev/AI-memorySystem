# Piano Prodotto — dal PoC dimostrato al prodotto definitivo

> **Origine:** brainstorming reviewer+utente del 2026-07-15, il giorno della demo riuscita.
> **Sostituisce** come piano operativo: `08_piano_correzione.md` (completato) e la vecchia
> roadmap a fasi `01-07` (archiviati in `docs/archive/`). **Non sostituisce**:
> `00_regole_procedure.md` (regole valide), `docs/SPEC.md`, il registro `10` (storico demo).
> La visione prodotto vive in `docs/concorso/Progetto.md` (north star): ogni feature lì
> promessa è agganciata a un'epica qui.

## Decisioni vincolanti (utente, 15/07)

1. **La demo non si tocca**: worktree `../AI-memorySystem-demo` (branch `demo-mercoledi`
   @ `788c49b`) è la copia funzionante permanente. Mai rimuoverla, mai mergiarla,
   ricollaudare le sue query (file 14) dopo ogni re-ingestion delle collections.
2. **Obiettivo**: prodotto definitivo per contesti reali, margini d'errore bassissimi,
   "non lo so" onesto dove non arriva. Nessuna scadenza esterna: si lavora bene, non in fretta.
3. **Target di gate ALTA**: precision **≥ 0.98** sul golden dataset (decisione utente:
   1.0 come vincolo di gate è troppo rigido). Nota di design del reviewer: il principio
   fail-closed di CLAUDE.md resta l'obiettivo architetturale — lo 0.98 è la soglia sotto
   cui il gate BLOCCA, non una licenza di sbagliare; ogni ALTA errato trovato va trattato
   come bug da eradicare, non come budget da spendere.
4. **Hardware**: si ottimizza per QUESTO Mac (M1 Pro 32GB). Il DGX Spark (GB10, ARM+CUDA,
   128GB unificati) arriverà tra mesi se finanziato: l'architettura deve avere **doppio
   profilo** (mac-ottimizzato ora, nvidia-ottimizzato poi) — mai nuovi hardcode MPS/MLX
   fuori dagli adapter/Port.
5. **UI del prodotto**: salto diretto a **Chainlit** (auth, sessioni utente e feedback
   nativi = fondamenta della memoria utente). Streamlit resta solo nella demo congelata.
6. **Feedback raccolto da subito**: lo schema si progetta in epica A anche se la memoria
   utente arriva in F — i dati si accumulano da ora.

## Le epiche (ordine di esecuzione)

### A — Un solo sistema (fondamenta)
Il lavoro vero vive su `feature/fase1-model-multiplexer`; main è 60+ commit indietro.
- [x] Merge `feature/fase1-model-multiplexer` → `main`; suite verde prima e dopo. (`468244b`)
- [x] Pulizia ~16 branch mergiati; decisione su `feat/fase-d2-quote` (bozza preventivo). (`468244b`)
- [x] **UI Chainlit** collegata a `answer(query, role)` del grafo tri-intent: chat con
  cronologia, badge fiducia, citazione, viewer documenti/esplosi (pagina citata SEMPRE
  per prima — il fix D-3.3 della demo rifatto pulito), fino a 8 candidati, feedback
  strutturato nativo. Schema feedback: (utente, query, risposta, verdetto, correzione).
  **Requisito utente (19/07, dal primo collaudo A7.1a):** la pagina citata va MOSTRATA
  in chat (render dell'immagine, non solo il riferimento) e il viewer deve diventare
  **navigabile**: zoom sulla tavola e scorrimento tra le pagine del documento. Il render
  in chat entra in A7.2; la navigazione completa (zoom/sfoglia) può maturare tra A7.2 e
  l'epica E, ma è un impegno di prodotto: non va persa. (`206bcf1`)
- [x] T3: fine dei fail-safe silenziosi — warning strutturato per ogni ramo degradato,
  `/health` con stato rami e modello residente. (`0f50405`, `7cd9730`)
- [x] Pool connessioni Postgres RO (TODO noto pipeline.py:623). (`d85dae0`)
- [x] `scripts/smoke_e2e.py` (3 query, una per ramo, exit≠0 se degrado) come gate pre-merge. (`96249c6`)
- [x] Cervello di produzione configurato: Ollama gemma4 (think off) + fallback qwen2.5 (A9.4 deciso, commit c7a6249, 2026-07-19).
- [x] Coda docs: aggiornare CLAUDE.md (stack reale) e README; archiviare ciò che A rende obsoleto.

### B — Misura (il metro di "irrifiutabile")
- **B1 Audit diagnostico** (prima i numeri, poi i target):
  a. 82 PDF fuori dall'indice (59/141): censimento con motivo per ciascuno;
  b. 58% dei codici degradati: campione stratificato, root-cause per categoria
     (estrazione tabelle? normalizzazione? chunking? fusione? generazione?);
  c. estrazione Mamoli morta a pagina 185: dove e perché;
  d. visivo 40% sul set eval: analisi errori per lingua e per tipo di pagina.
- [x] **B2 Golden dataset gate**: comando unico, baseline salvata, fail su regressione,
  metriche PER LINGUA. Target fissati sui numeri di B1; vincoli già decisi:
  precision ALTA ≥ 0.98 e (a regime, con l'epica H) **copertura ≥ 90% di risposte
  corrette e utili — il "non lo so" deve essere raro**. Include test di stabilità
  ×N run (lezione B3/B5).

### C — Estrazione di massa affidabile (il cuore: i dati)
- Ridisegno del binario strutturato sui numeri di B1: copertura 141/141 PDF,
  campi verbatim garantiti (il collasso multi-riga di Mamoli p8 non deve più esistere).
- Censimento scarti con motivo + **dashboard di risoluzione umana** (promessa della visione).
- Riconciliazione Fase 2 ripensata (oggi 11 linked / 2320 orfani per design sbagliato).
- Il ramo prezzi entra nel prodotto SOLO quando passa il gate B2.

**Decomposizione operativa (reviewer v4, 20/07/2026 — nomenclatura `punto.lettera` come B1.a):**
- **C1 — Rotta di ingestion autonoma (copertura degli 88 PDF scoperti).**
  - **C1.a** — attivazione ingestion **testo** (BGE-M3+BM25 → `aike_chunks`) via il
    percorso watchdog già scritto e testato (`src/watchdog/`), scope i **soli 88
    MAI_TENTATO**. Task 1: schema `tracked_files` + runner con `--dry-run` + test
    rosso-prima + **pilota su 3 file** (STOP reviewer). Task 2: ingestion restanti 85
    + golden gate + ricollaudo demo file 14.
  - **C1.b** — backfill **visivo** (`aike_pages_visual`, Qwen3-VL) degli 88.
  - **C1.c** — **strutturato** (`estrazione.righe_listino`) degli 88 — dopo C2 (routing).
  - **C1.d** (follow-on) — riconciliare i **54 già indicizzati** dentro `tracked_files`
    (mappatura slug→filename, ambiguità nota B1.a) + accensione daemon *live*.
- **C2 — Fix routing borderless Mamoli** (`is_table_page` prima del branch fast_text;
  baseline B1.c: 1.698 righe recuperabili vs 280 in DB — **verificato reviewer 20/07**:
  righe_listino Mamoli=280 (p8-185), p224-231=0).
  - **C2.a** (ATTIVO): nel branch FAST_TEXT di `run_massive_extraction.py:253`, prima del
    `continue`, esegui `is_table_page(page)` (che HA già il segnale borderless text-based:
    colonne allineate + numeric_ratio, table_detect.py:112-122, indip. dalle linee vettoriali)
    → se tabella, instrada al **TextTableExtractor** (text-layer, no VLM); altrimenti bypass.
    Rischio-gate ~nullo (tocca solo `righe_listino` Postgres, NON aike_chunks → demo non
    impattata; grounding fail-closed regge sui falsi-positivi). Persist è UPSERT (idempotente).
    DoD: righe Mamoli ↑ (target ~1.698, mecc. verificabile via psql), golden gate verde,
    TDD rosso-prima su pagina borderless reale (Mamoli p230: oggi 0 righe → dopo N).
- **C3 — Riconciliazione prezzi↔pagine** ripensata (11 linked / 2320 orfani).
- **C4 — Dashboard scarti** per risoluzione umana (i `tracked_files.status='failed'`
  con `error_log` di C1.a sono il primo alimentatore).
- **C5 — Bonifica igiene** (176 chunk Mamoli dup + 3 `_Test`; uniformare `doc`/`doc_stem`
  — tocca `aike_chunks`, richiede ok esplicito + ricollaudo demo).

**ESITO PILOTA C1.a-1 (20/07, verificato coi tool) → C1 SOSPESA, pivot a C2:**
Il pilota di 3 doc su 88 ha fatto **regredire il golden gate** (N003 narrativa IT:
PASS/MEDIA → MISS/BASSA). Rollback eseguito, produzione ripristinata (aike_chunks 32976,
documents 59, gate di nuovo exit 0). Diagnosi reviewer (misura reale, poi ripulita): lo
score RRF di manzoni_B p14 è **identico** con/senza i 3 doc (nessun crollo IDF), e manzoni
resta nel routed top-20 → **la causa NON è una soglia di profondità del retrieval**: il MISS
nasce a valle, nel percorso `answer()` (rerank sotto filtro-ruolo / confidence / citazione).
**Implicazione:** l'ingestion di massa (C1) destabilizza le risposte narrative esistenti in
modo non banale; va sbloccata solo dopo un lavoro di robustezza del retrieval/answer (materia
E/D), NON con un semplice "alza k". Per non fermare il progresso si passa a **C2** (routing
Mamoli: recupera dati reali senza aggiungere doc che competono → rischio-gate ~nullo).
**TRIGGER RIAPERTURA C1 (prioritaria, non abbandonata):** riaprire C1 quando esiste una
robustezza retrieval/answer che assorba l'ingresso di nuovi documenti SENZA far regredire il
golden gate (il finding N003 = MISS a livello answer, non retrieval-depth). Fino ad allora
gli 88 PDF restano scoperti per scelta consapevole, non per dimenticanza.
NB affidabilità esecutore: **2 report su 2 fabbricati** in questo ciclo (il secondo consegnato
mentre il suo processo era ancora in esecuzione); da qui, per ogni task: comando+stdout grezzo
non editabile e ri-verifica meccanica totale del reviewer prima di qualunque verdetto.

**Fondamento architetturale di C1 (analisi reviewer, verificata coi tool 20/07):** un
profilo brand NON è un prerequisito di ingestion (`src/watchdog/profile.py:21-26` →
fallback `default` passthrough); la precisione ("mai un ALTA falso") è garantita a valle
dal **guardrail**, indipendente dal profilo (B1.b: 0 promozioni ad ALTA su campione
sporco). Perciò l'autonomia sui PDF di clienti nuovi si ottiene **accendendo l'ingestion
autonoma** (profilo = raffinamento opzionale di recall), non curando i brand a mano.
L'auto-detector delle code-families (opzione "C" del brainstorming) è rimandato: attacca
una causa minoritaria (B1.b: ≥62% del degrado codici è dato povero/estrazione, non
normalizzazione) e presuppone comunque C1.a.

### D — Correttezza e stabilità della risposta
- Ramo STRUCTURED deterministico: l'LLM **formatta, non genera** (come da visione) —
  mai più "IEC 34-7" quando l'evidenza dice "EN60034-7".
- Stabilità ×N nel golden gate; audit del confidence (il caso "margine 1.00 → alta"
  su retrieval fuori corpus visto in demo va capito).
- La copertura ALTA sui codici (oggi ~42%) sale col lavoro C+D; target da B2.

### E — Visivo performante (su questo Mac)
- Latenza: modelli residenti tra le query (multiplexer condiviso nel serving UI, non
  ricreato per chiamata), batch nel reranker, pool adattivo (grande se margine basso).
- Recall: eval ampliato oltre le 15 query (anche col feedback della UI), analisi
  cross-lingua IT (il tallone), tuning tile_k/k su numeri.
- Opzioni future primo stadio (dal registro): Ops-Colqwen3-4B, Qwen3-VL-8B, 2048d.

### F — Memoria a tre livelli e apprendimento dai feedback
- Identità reale via auth Chainlit; RBAC per utente.
- **Requisito utente (19/07, dal collaudo UI): contesto conversazionale di sessione**
  — oggi ogni domanda è indipendente (answer stateless, scelta fase 0); il prodotto
  deve capire i riferimenti alla conversazione in corso ("e per il modello più
  grande?"). Da progettare qui con vincolo epica D: il contesto non deve mai diluire
  il grounding (affermazioni sempre ancorate a evidenza recuperata, mai alla sola
  chat precedente).
- **Tre livelli di memoria** (decisione utente 15/07):
  1. aziendale (generale, condivisa da tutti);
  2. di categoria (amministrazione/tecnici/commerciali… — agganciata al RBAC:
     "cosa si può dire a chi");
  3. personale ("Mario Rossi": domanda ricorrente + feedback → risposta riproposta).
- Feedback loop con **promozione verso l'alto**: una risposta (anche trovata online,
  epica H) confermata dagli utenti sale personale→categoria→aziendale e diventa la
  prima risposta per chi la cerca dopo. Promozione MAI da un singolo feedback:
  soglia di conferme o moderatore (issue #13) — da affinare in fase di design.
- Profilo utente compresso (stile L0-L3 della visione) iniettato nel system prompt.
- Arriva DOPO C/D/E: si memorizza sopra risposte stabili, non sopra difetti mobili.

### H — Risposta a cascata e conoscenza aperta (decisione utente 15/07)
Il "non lo so" deve diventare RARO: target di prodotto ≥90% risposte corrette e utili.
- Cascata: 1) corpus interno (doc+pagina) → 2) **agente di ricerca web** che ragiona,
  cita SEMPRE le fonti (URL) e passa dalla sandbox di igienizzazione → 3) conoscenza
  dell'LLM ("capitale della Francia" → risponde) MA sempre verificata online via
  agente di ricerca, con fonti.
- Principio evoluto: da "mai fuori dal corpus" a **"mai un'affermazione senza fonte"**;
  ogni risposta etichetta la provenienza (corpus / web / conoscenza verificata).
- Codici e prezzi: dal corpus = garantiti dal guardrail (ALTA); trovati SOLO online =
  mostrati con **warning ben visibile** ("trovato online, correttezza NON verificata"
  + fonte), mai confusi con le risposte a catalogo; la conferma degli utenti (epica F)
  li promuove a risposta aziendale condivisa.
- RAG documentale generico (brochure → "cosa fa questa azienda?"): potenziare NARRATIVE
  su documenti non-catalogo, coperto dal golden gate.
- Vincoli: uscita web configurabile per cliente (air-gapped ⇒ cascata ferma al liv. 1);
  latenza della verifica web non deve rallentare le risposte da corpus.
- Posizione: dopo D (la cascata si costruisce sopra risposte interne stabili);
  può intrecciarsi con E/F secondo le priorità del momento.

### G — Doppio profilo hardware (trasversale, sempre attivo)
- Ogni epica: nessun nuovo hardcode MPS/MLX/32GB fuori dagli adapter.
- Config a profili (mac/nvidia): modelli, quantizzazioni, max_resident, batch size.
- All'arrivo del DGX: epica di migrazione dedicata (serving CUDA: vLLM/TensorRT-LLM/
  llama.cpp; MLX resta il profilo mac).

## Metodo (quello che ha funzionato)
- Un'epica alla volta; ogni epica spezzata in punti piccoli con DoD e STOP.
- Agente esecutore implementa; reviewer verifica OGNI claim con i tool prima del verdetto.
- Da B2 in poi: NESSUN punto di NESSUNA epica si chiude senza `python scripts/golden_gate.py` verde.
- Commit piccoli; niente push su main senza smoke e2e.

## Governance documenti
- **Vivi**: questo file (piano operativo), `concorso/Progetto.md` (visione, aggiornata
  a fine epica), `00_regole_procedure`, `SPEC.md`, CLAUDE.md/README (aggiornati in A).
- **Storici** (non si toccano): `10` (registro demo), `12` (handoff), `13`/`14` (demo).
- **Archiviati** in `docs/archive/`: roadmap `01-07`+`01b`, `08` (completato), `09`,
  `PIANO-IMPLEMENTAZIONE.md`, eval/report superati.
