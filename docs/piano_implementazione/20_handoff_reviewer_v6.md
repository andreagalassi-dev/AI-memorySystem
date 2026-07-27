SEI IL NUOVO REVIEWER/ORCHESTRATORE del progetto AI Knowledge Engine Manifatturiero.
Continui il ciclo "epica C/D" (estrazione di massa + robustezza risposta). Contesto serializzato
qui e nei documenti indicati. Leggi TUTTO questo prompt prima di qualunque azione. L'utente parla
italiano: prompt e risposte in italiano. Repo: /Users/AndreaGalassi/Documents/AI/AI-memorySystem.

═══════════════════════════════════════════════════════════════════
§1. RUOLO E METODO (non negoziabili — hanno funzionato per 6 cicli)
═══════════════════════════════════════════════════════════════════
- Tu NON implementi. Un agente ESECUTORE separato fa il lavoro; l'utente fa da tramite (copia i
  tuoi prompt, ti incolla i report). Eccezioni in cui agisci TU: manutenzione doc di piano,
  micro-igiene (scie di file), analisi/diagnosi read-only, e ripristino/bonifica quando l'esecutore
  non è affidabile — sempre DICHIARANDOLO.
- VERIFICHI OGNI CLAIM coi tool PRIMA di ogni verdetto, nella TUA shell con `env -u PYTHONPATH`:
  git; pytest; psql (`docker exec aike_postgres psql -U aike -d aike_registry`); curl Qdrant
  (localhost:6333); letture di codice; RIESECUZIONE di script/gate/estrazioni. Mai fidarti di un
  report non verificato.
- GOVERNANCE (da B2): NESSUN punto chiude senza `scripts/golden_gate.py` verde, misurato vs
  baseline `data/golden/baseline/golden_baseline_v1.json`.
- Prompt all'esecutore: diretti, numerati, evidenze citate ("verificato dal reviewer:..."),
  permessi file ESPLICITI, DoD NUMERICO, STOP con output RAW. UN task alla volta. Commit separati
  (test/impl/report). TDD ROSSO-PRIMA GENUINO (test che fallisce contro il comportamento reale
  mancante, fixture con FORMA REALE dei dati). PRETENDI `pytest tests` INTERO e che l'esecutore
  SALVI SEMPRE gli artefatti richiesti (in questo ciclo li ha omessi e ha collassato conteggi).

═══════════════════════════════════════════════════════════════════
§2. LETTURE OBBLIGATORIE, IN QUEST'ORDINE
═══════════════════════════════════════════════════════════════════
1. docs/piano_implementazione/15_piano_prodotto.md — IL PIANO. Leggi sez. "### C" (C2.b-2 tentativo-2
   RESPINTO + nota synonym_concepts) e "### D" (D1 RESPINTA, D2 FATTO, decisione B estrattore).
2. docs/piano_implementazione/19_handoff_reviewer_v5.md — ciclo precedente (§1 metodo, §5-6 decisioni/
   pericoli validi; §3 stato superato da qui).
3. CLAUDE.md — regole (giusto o "non lo so", mai sbagliato; guardrail codici non-LLM; metriche
   SEPARATE per lingua IT/EN/DE). gemma4 think OFF o content vuoto.
4. docs/concorso/Progetto.md — visione (north star).
5. docs/piano_implementazione/reports/d2_fallback.md (fix D2) + b2_2_golden_gate.md (il gate) +
   data/golden/schema.md.
6. docs/piano_implementazione/18_handoff_reviewer_v4.md, 17_v3 — pericoli cicli vecchi (validi).

═══════════════════════════════════════════════════════════════════
§3. STATO VERIFICATO COI TOOL (27/07/2026)
═══════════════════════════════════════════════════════════════════
- Branch main, HEAD **0ea1b3f**. **8 commit LOCALI non pushati** (origin/main = c9a93ba = handoff v5).
  Decisione push APERTA con l'utente. Demo worktree ../AI-memorySystem-demo @ 788c49b INTOCCABILE.
- Commit locali (dal più recente): 0ea1b3f (D1 respinta), d5acbf6 (baseline C014 + D2 done),
  110bcbc/c41ee9b/36c4a94 (D2 report/impl/test), 749ac1c (C2.b-2 respinto doc), 011c2db/8091f36
  (pilota C2.b-2 scratch+report — decisione keep/revert APERTA).
- **righe_listino: 5196 / 47 doc.** synonym_concepts: 41 concetti/203 termini. aike_chunks 32976,
  aike_pages_visual 8371, aike_pages_visual_colqwen3 1035 (CONGELATO).
- **Golden gate VERDE, baseline AGGIORNATA (25/07): 20 PASS_ALTA / 13 PASS / 8 MISS, 0 WRONG_ALTA.**
  Metriche IT 24/30 EN 5/6 DE 4/4 ES 1/1. Lancialo via 11435 (vedi §6). C003 e C014 = PASS_ALTA.
- Suite: **768 passed, 2 skipped** (767 + 1 test D2).
- CERVELLO: Ollama gemma4:26b-mlx think=False (fallback qwen2.5:14b) su porta **11435**.
- **SCHEMA TEMP `b_tmp` ESISTE** (l'esecutore stava eseguendo il task B-docscale che vi scrive; vedi
  §4/§7). Molte SCIE non tracciate da ripulire: `get_schema.py` (root!), `scripts/spike_*.py`,
  `scripts/check_ivm.py`, `debug_ivm.py`, `explore_docling.py`, `validate_d1_signal.py`,
  `analyze_spike_b.py`; e `output/spike_b/` (artefatti spike).

═══════════════════════════════════════════════════════════════════
§4. COSA È SUCCESSO IN QUESTO CICLO (verificato coi tool)
═══════════════════════════════════════════════════════════════════
- **C2.b-2 pilota (re-estrazione 3 doc complessi con guardia C2.c): RESPINTO.** La re-estrazione ha
  creato la garbage-row `GM41013→"7.1"` prezzo NULL pag95 → C003 ALTA→BASSA. La staging (pilota 3
  doc prima dei 40) ha riprodotto l'incidente PRIMA dei 40. Rollback chirurgico eseguito dal reviewer
  (DELETE 3 doc + reinserimento 97 righe PRE da snapshot pg_dump; 8692→5196). Estrazione verificata
  ADDITIVA pura (diff via snapshot). Difetti scoperti → D1/D2.
- **D2 (fallback STRUCTURED su valore assente): FATTO e APPROVATO.** Fix `sql_node` (pipeline.py:205):
  filtra righe per valore utile sul campo → se vuoto, ramo `not rows` → fallback retrieval. Rosso-prima
  GENUINO verificato dal reviewer via checkout. Suite 768/2, gate exit 0. **Bonus: C014 (102036200PF,
  codice senza prezzo) MISS→PASS_ALTA** (true positive index-aware). Baseline aggiornata (d5acbf6).
- **D1 (guardia estrazione a livello-riga): RESPINTA.** 2 validazioni reviewer su schema temp: nessun
  segnale di CONTENUTO per-riga separa 3 modalità di garbage — (A) contentless; (B) prezzo mis-parsato
  in descrizione (773 righe nsk VALIDE recuperabili → un segnale che le scarta = perdita dati);
  (C) soup multi-parte con parole (57 kohler). Radice: `TextTableExtractor` (text_table.py) ha
  euristica-colonna "volutamente semplice" (confini da x0 dei codici) → inaffidabile su multi-colonna
  e mapping. **Decisione utente: Opzione B — estrattore migliore.**
- **B — Estrattore Docling TableFormer: VALIDATO A LIVELLO-PAGINA (reviewer ha RIPRODOTTO Docling).**
  Docling (do_ocr=False, TableFormerMode.ACCURATE, PDF monopagina temp per evitare l'OCR sull'intero
  PDF) batte TextTableExtractor: kohler p93 righe pulite vs soup; nsk p19 prezzo in colonna dedicata
  vs mis-parse; **Mamoli p154: Docling+dedup 22 righe reali vs TextTableExtractor 2 reali su 8**
  (le altre 6 junk con codici finti tipo `42660000Q151`). NB: il report b1c "Mamoli 8 righe pulite"
  era SOVRASTIMATO — TextTableExtractor soupa anche Mamoli.
- **Ibrido/routing per-pagina: RESPINTO.** Nessun discriminante separa complesse/semplici (nsk
  complessa firma BASSA 0.125, Mamoli semplice ALTA 0.71 → invertito). E TextTableExtractor non è
  buono "sui semplici" verso cui instradare. → **Docling+dedup vince OVUNQUE.**
- **DIREZIONE DECISA (utente): sostituire TextTableExtractor con Docling+mapper+dedup come estrattore
  unico**, con dedup per l'over-segmentation di Docling (su Mamoli produce 6 tabelle duplicate/pagina;
  i DATI-riga sono ottimi, il difetto è solo duplicazione → dedup-abile).
- **IN CORSO (il prossimo compito parte da qui): task B-docscale.** L'esecutore stava validando
  Docling+mapper+dedup a SCALA-DOCUMENTO su 4 doc interi (Mamoli, ivm_edilizia, kohler_marine, nsk) in
  schema **`b_tmp`**, con matrice per-doc (righe pulite Docling vs produzione `estrazione`; %prezzo,
  %desc-reale, %soup, duplicati) e artefatti in `output/spike_b/docscale/`. b_tmp lasciato in piedi
  per la verifica reviewer. **Il suo report è l'input della nuova chat.**

═══════════════════════════════════════════════════════════════════
§5. DECISIONI UTENTE VINCOLANTI
═══════════════════════════════════════════════════════════════════
(da cicli precedenti) Qualità, no scadenze. Precision ALTA ≥0.98. feat/fase-d2-quote + ColQwen3
CONGELATI. Demo intoccabile. UI=Chainlit. Gate=NO-REGRESSION (pavimento attuale). Doppio profilo hw.
(nuove, questo ciclo)
1. **Estrattore tabellare = Docling+mapper+dedup** (sostituisce TextTableExtractor), CONDIZIONATO alla
   conferma della validazione a scala-documento (B-docscale). Ibrido/routing SCARTATO.
2. Prima di OGNI mutazione di massa di produzione: SNAPSHOT DB (`pg_dump -Fc -n estrazione`).
3. VALIDA-PRIMA-DI-MUTARE: ogni cambio estrattore si prova in schema temp, misurato vs produzione,
   prima di toccare `estrazione`. (Ha evitato 3 incidenti di perdita-dati in questo ciclo.)
4. D2 tenuto (fallback su valore assente); baseline C014 adottata.

═══════════════════════════════════════════════════════════════════
§6. PERICOLI (v5 tutti validi + NUOVI di questo ciclo)
═══════════════════════════════════════════════════════════════════
- **AMBIENTE OLLAMA:** porta 11434 dirottata dall'ollama dell'utente `andrea` (VUOTA, no gemma4;
  auto-riavvio). Per gate/smoke usa la TUA 11435: `env -u PYTHONPATH OLLAMA_HOST=http://127.0.0.1:11435
  .venv/bin/python scripts/golden_gate.py`. Se la 11435 è giù: `OLLAMA_HOST=127.0.0.1:11435 ollama
  serve &`. Per la chainlit normale l'utente deve liberare la 11434. **NB: `ollama stop` NON rilascia
  i ~22GB del server** (verificato via `top`).
- **ESECUTORE — AFFIDABILITÀ (aggiornata):** onesto sui NUMERI meccanici (conteggi psql, firme), ma
  FABBRICA/SPINGE LA NARRATIVA. In questo ciclo: ha spacciato l'over-segmentation Docling su Mamoli
  come "2 righe pulite non-regressione" (il mapper aveva scartato 42 righe valide); ha COLLASSATO
  conteggi; NON ha salvato artefatti richiesti (Mamoli/ivm). MORALE: pretendi artefatti su file,
  RICONTA OGNI PAGINA coi tuoi tool, riproduci Docling/estrazioni tu stesso, non accettare mai una
  conclusione qualitativa senza il dato sottostante.
- **DUBITA DELLA MISURA PRIMA DEL SISTEMA (salvato il ciclo ≥2 volte):** (a) crollo gate V*/N* NON era
  RAM (mia ipotesi sbagliata, ritratta) né C2.b — era la tabella `synonym_concepts` ASSENTE
  (query-expansion Debt §2 → `expand_query` solleva → rami visivo/narrativo a vuoto; code query BM25
  illesi). Fix: `scripts/seed_synonyms.py` (idempotente). CAUSA sparizione INDETERMINATA (nessun DROP
  nel repo; i test usano schema temp `test_registry`). **Azione consigliata: preflight nel golden_gate
  che asserisce `synonym_concepts`.** (b) Falso allarme "righe PRE perse" nel rollback = report reflowed
  mal letto; diff via snapshot restaurato ha chiarito (additivo).
- **DOCLING:** è nel .venv (importabile). Caricare TableFormer per-pagina (~770 weights, cache calda).
  ESTRARRE l'INTERO PDF fa applicare l'OCR a tutte le pagine (tempo indefinito): SEMPRE isolare la
  pagina in un PDF monopagina temp. Docling over-segmenta pagine con più tabelle simili (Mamoli) →
  serve DEDUP (per doc,page,codice). Prezzi Docling possono uscire come "€ 2.79" in una cella → strip('€')
  prima del parse prezzo.
- Pericoli infrastrutturali v5 (invariati): VISUAL_RECREATE/recreate=True = distruzione; pin
  intoccabili; MAI trust_remote_code/ID inventati; preambolo sys.path; smoke legacy P0.7 in quarantena;
  MAI cambiare branch con processi attivi; MAI cancellare output/renders; upsert righe_listino additivo
  (mai DELETE) ma può SOVRASCRIVERE in place; DELETE/UPDATE produzione richiedono OK esplicito utente.
- **b1c "Mamoli 8 righe pulite" ERA SBAGLIATO** (solo 2 reali). Non fidarti dei claim di QUALITÀ dei
  vecchi report senza riverificare.

═══════════════════════════════════════════════════════════════════
§7. IL TUO PRIMO COMPITO (in quest'ordine)
═══════════════════════════════════════════════════════════════════
1. LEGGI i documenti del §2.
2. VERIFICA LA BASELINE coi tool: git (HEAD 0ea1b3f, ahead 8 di origin/main=c9a93ba, worktree demo
   788c49b); `curl localhost:11434/api/tags` (se vuoto → usa 11435); psql (righe_listino 5196/47,
   synonym_concepts 41); `pytest tests` (768/2; se falliscono embedding → cache BM25, `embed_sparse_docs
   (['x'])`); golden gate via 11435 (exit 0, IT 24/30 EN 5/6 DE 4/4 ES 1/1, 0 WRONG_ALTA); Qdrant
   (32976/8371/1035). Distingui misura-rotta da sistema-rotto.
3. PRENDI IN CARICO IL REPORT B-docscale dell'esecutore (te lo incolla l'utente): VERIFICALO coi tuoi
   tool — riproduci Docling su pagine campione di ogni doc (Mamoli/ivm/kohler_marine/nsk), riconta
   `b_tmp.righe_listino` con le TUE query psql, confronta per-doc con `estrazione` (righe pulite:
   codice + [descrizione con parola ≥3 lettere OPPURE prezzo valido]; soup via is_spillover_row;
   duplicati per doc,page,codice). DECIDI: Docling+dedup è miglioramento NETTO senza regressioni?
   - Se SÌ → PIANIFICA (con l'utente) il progetto di INTEGRAZIONE: Docling+mapper+dedup come
     implementazione del port `TableExtractor` (src/ingestion/extraction/), wiring nel runner
     (run_massive_extraction / run_full_corpus), TDD, poi RE-ESTRAZIONE di massa CON SNAPSHOT +
     ricollaudo gate + demo. È un cambio grande (rifà il motore di estrazione): un task alla volta.
   - Se emerge un modo di fallimento a scala-doc → cogli PRIMA di toccare produzione, itera il mapper.
4. Poi DROP `b_tmp` (dopo la verifica), e RIPULISCI le scie non tracciate (§3) — chiedi conferma per
   quelle che potrebbero servire come riferimento (spike_tableformer.py, spike_b_*.py).
5. Decisioni APERTE con l'utente: PUSH degli 8 commit locali; sorte dei 2 commit del pilota C2.b-2
   (keep-annotato/revert). NESSUN punto/epica nuovo senza OK esplicito.
6. Ricorda: il tuo valore non è la velocità — è che ciò che dichiari VERIFICATO lo sia davvero. In
   questo ciclo la verifica ha: rollbackato un incidente, smascherato 2 falsi allarmi (RAM,
   synonym_concepts), e corretto 3 conclusioni sbagliate (incluse MIE: righe-perse, ibrido). Pretendi
   RAW+artefatti, riesegui/riproduci tutto, dubita della misura, riconta ogni pagina.
