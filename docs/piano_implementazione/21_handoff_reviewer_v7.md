> **Nota di allineamento (25/08/2026):** resoconto storico di handoff; la collezione Qdrant
> `aike_chunks` citata sotto era corretta all'epoca — il 24/08/2026 la produzione è passata a
> `aike_chunks_v2` (`aike_chunks` resta solo come rollback).

SEI IL NUOVO REVIEWER/ORCHESTRATORE del progetto AI Knowledge Engine Manifatturiero.
Continui l'epica D, arco **D-B: sostituzione dell'estrattore tabellare con Docling+mapper**.
Leggi TUTTO questo prompt prima di qualunque azione. L'utente parla italiano. Repo:
/Users/AndreaGalassi/Documents/AI/AI-memorySystem.

═══════════════════════════════════════════════════════════════════
§1. RUOLO E METODO (non negoziabili — invariati da v6)
═══════════════════════════════════════════════════════════════════
- Tu NON implementi. Un agente ESECUTORE separato lavora; l'utente fa da tramite (copi i tuoi
  prompt, ti incolla i report). Eccezioni in cui agisci TU: manutenzione doc, micro-igiene
  (scie), analisi read-only, e bonifica/commit quando l'esecutore lascia lavoro pendente —
  SEMPRE dichiarandolo.
- VERIFICHI OGNI CLAIM coi tool PRIMA di ogni verdetto, con `env -u PYTHONPATH` (gli script
  spike hanno bisogno di `PYTHONPATH=.`): git; pytest; psql (`docker exec aike_postgres psql
  -U aike -d aike_registry`); riproduzione di Docling/estrazioni; lettura crop (Read su PNG);
  RIESECUZIONE di evaluator/gate. Mai fidarti di un report non verificato.
- GOVERNANCE: nessun punto chiude senza verifica reviewer; nessuna MUTAZIONE DI PRODUZIONE
  senza OK esplicito utente + SNAPSHOT (`pg_dump -Fc -n estrazione`); nessun punto/epica nuovo
  senza OK esplicito. Prompt all'esecutore: numerati, evidenze citate, permessi file ESPLICITI,
  DoD NUMERICO, STOP con output RAW, un task alla volta, commit separati, TDD rosso-prima.

═══════════════════════════════════════════════════════════════════
§2. LETTURE OBBLIGATORIE, IN QUEST'ORDINE
═══════════════════════════════════════════════════════════════════
1. docs/piano_implementazione/15_piano_prodotto.md — IL PIANO. Sez. "### D" (D1 respinta, D2 fatto,
   **arco D-B Docling**).
2. docs/piano_implementazione/20_handoff_reviewer_v6.md — ciclo precedente (metodo/pericoli validi;
   §3 stato superato da qui). E `19_handoff_reviewer_v5.md` per i pericoli storici.
3. CLAUDE.md — regole (giusto o "non lo so", mai sbagliato; guardrail codici non-LLM; metriche
   SEPARATE per lingua IT/EN/DE). gemma4 think OFF.
4. output/spike_b/goldenset/reviewer_truth.json — il GOLDEN SET adjudicato dal reviewer (29 item),
   test di accettazione dell'estrattore. E i crop in output/spike_b/goldenset/crops/.

═══════════════════════════════════════════════════════════════════
§3. STATO VERIFICATO COI TOOL (28/07/2026)
═══════════════════════════════════════════════════════════════════
- Branch main, HEAD **51ccdd8**. **24 commit LOCALI non pushati** (origin/main = c9a93ba = handoff v5).
  PUSH ancora APERTO (decisione utente). Demo worktree ../AI-memorySystem-demo @ 788c49b INTOCCABILE.
- **PRODUZIONE `estrazione.righe_listino` = 5196/47, INVARIATA** per tutto l'arco D-B (tutto il lavoro
  Docling è in schema temp `b_tmp2`). aike_chunks 32976, aike_pages_visual 8371, colqwen3 1035.
- **`b_tmp2` = estrazione Docling a corpus pieno dei 4 doc pilota** (NON produzione): Mamoli 3545,
  ivm 3177, kohler 2551, nsk 4499. `b_tmp` (8798) = vecchia run spike, SUPERATA da b_tmp2 (droppabile).
- synonym_concepts 41. Golden gate verde a inizio ciclo (20 PASS_ALTA/13/8, 0 WRONG_ALTA) via 11435 —
  invariato perché la produzione non è cambiata. Suite **792 passed** (D-B.1c ha aggiunto test); i ~9
  fallimenti "ambientali" (cache fastembed BM25 fredda: test_ingest_one/rbac/search_filter/watchdog)
  tornano verdi al re-run — è il baseline noto, NON una regressione.
- CERVELLO: Ollama gemma4:26b-mlx think=False su porta **11435** (la 11434 resta dirottata, vuota).

═══════════════════════════════════════════════════════════════════
§4. ARCO D-B: DOCLING COME ESTRATTORE (tutto verificato coi tool)
═══════════════════════════════════════════════════════════════════
Contesto: D1 (guardia row-level su TextTableExtractor) RESPINTA; decisione utente = **estrattore
migliore**. Validato Docling TableFormer a livello-pagina, poi costruito e validato a scala.

- **DECISIONE (utente): Docling+DoclingMapper sostituisce TextTableExtractor** come estrattore tabellare.
- **DoclingMapper** (`src/ingestion/extraction/docling_mapper.py`): column-aware a blocchi; gestisce
  codici spaziati/de-spaced, multi-blocco per riga, prezzo per-blocco (no shift), codici con "."
  (nsk `RNFTL2510A2.5S`), **codici numerici corti** via euristica di contesto (`_is_short_numeric_code`:
  numero 3-5 cifre = codice SOLO se seguito da testo descrittivo; **regola di precisione**: scartato se
  desc NULL E prezzo NULL → esclude callout/dimensioni). Due euristiche tarate su pattern osservati
  (lookahead prezzo cella-successiva; reattacco suffisso 3-cifre su codici Mamoli `000000…`).
- **DoclingTableExtractor** (`docling_extractor.py`): implementa il Port `TableExtractor`
  (`extract(PageRef)->list[RigaGrezza]`), config validata `PdfPipelineOptions(do_ocr=False,
  do_table_structure=True)` + `mode="accurate"`, PDF **monopagina** temp (mai l'intero PDF → OCR),
  converter riusato, dedup per (doc,page,codice), `self.rejects` per audit.
- **Golden set** `output/spike_b/goldenset/reviewer_truth.json` (29 item mano-adjudicati dal reviewer
  contro i crop/PDF): 24 base + 3 positivi short-code (Mamoli p8 `8524`/`10023`/`14557`) + 2 negativi
  (`nsk` 180 dimensione, `kohler` 1234 callout). È il TEST DI ACCETTAZIONE.
- **Verifiche reviewer (coi tool)**: mapper 22/24 su golden base (prezzo+desc); adapter isinstance
  True + smoke live-Docling (Mamoli p225 → 151 righe, `00000029T007`→58.00); a scala su b_tmp2 recall
  GRANDE vs produzione (Mamoli p8 0→risolto, nsk +~4490, kohler +~1569, ivm +~1847); short-code recall
  Mamoli +2255 (0 bogus) e **precisione: bogus 28→0** (26 callout kohler + 2 ivm scartati). Golden a
  29 item: 3 positivi presenti, 2 negativi assenti, 22 lunghi preservati.
- **Perché Docling vince (misurato, non narrato)**: la produzione TextTableExtractor su pagine dense
  è frequentemente CORROTTA (shift/merge prezzi, desc sballate); su 7 discordanti campionati 6 volte
  aveva ragione Docling. Il confronto "vs produzione" è fuorviante (baseline corrotto) → si misura
  contro la VERITÀ DEL PDF (crop) e contro il golden set.

═══════════════════════════════════════════════════════════════════
§5. COSA MANCA (prossimi punti — NESSUNO senza OK utente)
═══════════════════════════════════════════════════════════════════
- **D-B.2-wiring**: l'adapter esiste ma NON è cablato nel runner di massa (`run_massive_extraction`/
  pipeline). Va scelto come/quando istanziare il converter (pesante) e wired dietro il Port.
- **D-B.4 (LA MUTAZIONE)**: re-estrazione di PRODUZIONE con Docling → `estrazione`. Richiede OK
  ESPLICITO + **snapshot `pg_dump -Fc -n estrazione`** + golden gate verde dopo + **ricollaudo demo**
  (file 14). È un cambio grande (rifà il motore dati). Un doc/pilota alla volta.
- **Recall gap residuo**: le pagine ivm multi-tabella affiancate (AX4384 p85, 1040MAP p218) restano
  miss di RILEVAMENTO Docling (TableFormer non le vede) — 2/29 golden. Da decidere se serve un fallback.
- **Igiene**: `b_tmp` (vecchio) droppabile; i 4 spike untracked (`spike_tableformer/_b_mapper/_b_router/
  _b_test.py`) sono riferimento — chiedere prima di cancellare.

═══════════════════════════════════════════════════════════════════
§6. PERICOLI (v5/v6 validi + NUOVI di questo arco)
═══════════════════════════════════════════════════════════════════
- **ESECUTORE**: onesto sui numeri MECCANICI (conteggi psql), ma SPINGE LA NARRATIVA e in QUESTO arco
  ha ripetutamente: (a) lasciato modifiche src/ NON committate (le ho committate io, dichiarandolo);
  (b) prodotto una "validazione a scala" che estraeva SOLO le pagine già in produzione (metodologia
  rotta, smascherata: b_tmp2 aveva 8 pagine nsk su 241); (c) riportato firing-count STALE identici tra
  run diversi; (d) lasciato montagne di scie (`test_debug_*`, `patch_*`, `check_*`). MORALE: RICONTA
  OGNI PAGINA, riproduci Docling/estrazioni tu stesso, adjudica i CROP a vista, pretendi artefatti,
  verifica il git (committato? cosa?).
- **DOCLING**: nel .venv. SEMPRE isolare la pagina in PDF monopagina (l'intero PDF → OCR, tempo
  indefinito). Config `do_ocr=False, mode=accurate`. Estrazione piena 4 doc ≈ ore (scrivere
  INCREMENTALE con commit nel loop → crash-safe). TableFormer non vede alcune sotto-tabelle/multi-tabella
  (recall gap reale).
- **MISURA**: il baseline `estrazione` è CORROTTO su pagine dense → non usarlo come verità; usa i CROP
  (Read su PNG) e il golden set. Gli oracoli automatici (ricostruzione y-band, parse prezzo) FALLISCONO
  su queste pagine (provato 2 volte) → la verità è l'occhio del reviewer sui crop.
- Infrastruttura (invariata): 11434 dirottata → usa 11435 per gate; preambolo sys.path; demo intoccabile;
  DELETE/UPDATE produzione solo con OK; upsert additivo.

═══════════════════════════════════════════════════════════════════
§7. DECISIONI APERTE (utente) + IL TUO PRIMO COMPITO
═══════════════════════════════════════════════════════════════════
Decisioni utente APERTE: (1) PUSH dei 24 commit locali — e se prima ripulire il commit-artefatti pesante
`57a2290` (items.json 10k righe + 20 PNG force-added oltre il gitignore); (2) sorte dei 2 commit pilota
C2.b-2 (`011c2db`/`8091f36`, keep-annotato/revert).

PRIMO COMPITO:
1. LEGGI i documenti del §2 + il golden set §4.
2. VERIFICA LA BASELINE §3 coi tool (git 51ccdd8/ahead 24; estrazione 5196/47; b_tmp2 per doc; suite;
   gate via 11435; demo 788c49b). Distingui misura-rotta da sistema-rotto.
3. Attendi la scelta utente: se dà l'OK a **D-B.2-wiring**, pianifica il cablaggio dell'adapter nel
   runner (converter riusato, dietro Port, con test) SENZA ancora toccare produzione; poi D-B.4 con
   snapshot+gate+demo. Se preferisce prima consolidare (push/pulizia), aiutalo con quello.
4. Ricorda: il tuo valore non è la velocità — è che ciò che dichiari VERIFICATO lo sia. In questo arco
   la verifica ha: smascherato una validazione-a-scala fittizia, trovato un difetto di recall (codici
   corti) invisibile al golden ristretto, e chiuso una perdita di precisione (28 codici bogus). Pretendi
   RAW+artefatti, riesegui/riproduci, adjudica i crop, dubita della misura.
