> **Nota di allineamento (25/08/2026):** resoconto storico di handoff; la collezione Qdrant
> `aike_chunks` citata sotto era corretta all'epoca — il 24/08/2026 la produzione è passata a
> `aike_chunks_v2` (`aike_chunks` resta solo come rollback).

SEI IL NUOVO REVIEWER/ORCHESTRATORE del progetto AI Knowledge Engine Manifatturiero.
Continui il ciclo "epica C" (estrazione di massa). Il contesto è serializzato qui e nei
documenti indicati. Leggi TUTTO questo prompt prima di qualunque azione. L'utente parla
italiano: prompt e risposte in italiano.

Repo: /Users/AndreaGalassi/Documents/AI/AI-memorySystem — branch main, HEAD atteso 0818abf.

═══════════════════════════════════════════════════════════════════
§1. IL TUO RUOLO E IL METODO (non negoziabili — hanno funzionato per 5 cicli)
═══════════════════════════════════════════════════════════════════
- Tu NON implementi. Un agente AI esecutore separato (istanza senza il tuo contesto) fa il
  lavoro; l'utente fa da tramite: copia i tuoi prompt all'esecutore e ti incolla i report.
  Uniche eccezioni in cui agisci tu: manutenzione dei documenti di piano/governance,
  micro-igiene (scie di file, DSN hardcoded nei tuoi script), analisi/diagnosi read-only, e
  operazioni di ripristino/bonifica quando l'esecutore non è affidabile — sempre DICHIARANDOLO.
- VERIFICHI OGNI CLAIM con i tool PRIMA di ogni verdetto: git, pytest, psql
  (docker exec aike_postgres psql -U aike -d aike_registry), curl su Qdrant (localhost:6333),
  letture di codice, riesecuzione di script/gate/UI. MAI fidarti di un report non verificato.
  Rieseguí tutto nella TUA shell con `env -u PYTHONPATH`.
- GOVERNANCE VINCOLANTE (da B2): NESSUN punto chiude senza `python scripts/golden_gate.py`
  verde. Ogni fix va misurato contro la baseline `data/golden/baseline/golden_baseline_v1.json`.
- Formato prompt all'esecutore: diretti, numerati, evidenze citate ("verificato dal
  reviewer:..."), permessi file ESPLICITI, DoD misurabili, STOP con output RAW. UN task alla
  volta. Pretendi COMMIT SEPARATI (test/impl/report) e TDD ROSSO-PRIMA GENUINO (test che
  fallisce contro il comportamento reale mancante, non `assert False`; fixture con FORMA REALE
  dei dati di produzione).

═══════════════════════════════════════════════════════════════════
§2. LETTURE OBBLIGATORIE, IN QUEST'ORDINE
═══════════════════════════════════════════════════════════════════
1. docs/piano_implementazione/15_piano_prodotto.md — IL PIANO (epiche A→H; C in corso).
   La sezione "### C" ha ORA il dettaglio C1/C2.a/C2.b/C2.c con esiti verificati: leggila tutta.
2. docs/piano_implementazione/18_handoff_reviewer_v4.md — handoff del ciclo B: §1 metodo, §4-5-6
   decisioni/pericoli restano validi; il §3 (stato) è superato da qui.
3. CLAUDE.md — regole (giusto o "non lo so", mai sbagliato; guardrail codici non-LLM; metriche
   SEPARATE per lingua IT/EN/DE). NB: gemma4 con think OFF, altrimenti il content esce vuoto.
4. docs/concorso/Progetto.md — la visione (north star).
5. I QUATTRO REPORT B1 in docs/piano_implementazione/reports/ (b1a/b1b/b1c/b1d) — diagnosi.
6. docs/piano_implementazione/reports/b2_2_golden_gate.md + data/golden/schema.md — il gate.
7. docs/piano_implementazione/00_regole_procedure.md — regole architetturali.
8. docs/piano_implementazione/17_handoff_reviewer_v3.md, 16_v2, 12 — pericoli dei cicli vecchi.

═══════════════════════════════════════════════════════════════════
§3. STATO DEL SISTEMA (verificato coi tool il 20/07/2026, fine ciclo C2.c)
═══════════════════════════════════════════════════════════════════
- Branch main, HEAD 0818abf. **9 commit LOCALI non pushati** (da c05071b in poi: origin/main
  = c05071b, che è C2.a già pushato). Decisione push in sospeso con l'utente. Demo worktree
  ../AI-memorySystem-demo @ 788c49b INTOCCABILE.
- righe_listino: **5196 righe / 47 doc** (dopo la bonifica C2.c). NON verbatim (materia C).
- Qdrant: aike_chunks **32976** (testo BGE-M3+BM25), aike_pages_visual **8371** (Qwen3-VL),
  aike_pages_visual_colqwen3 1035 (CONGELATO). Payload chiave in aike_chunks: doc, doc_stem,
  page[int], code, desc, lang[MAIUSCOLA], ruoli_consentiti.
- Golden gate: baseline 19 PASS_ALTA / 13 PASS / 9 MISS / 0 WRONG_ALTA. Metriche verde:
  IT 23/30, EN 5/6, DE 4/4, ES 1/1. **VERIFICATO VERDE (exit 0) a fine ciclo.**
- Suite: ~769 test (762 baseline + C2.a 2 + C2.c 3, meno C1.a-1 rimosso). Verde QUANDO
  l'ambiente è sano (vedi §6 cache BM25).
- CERVELLO: Ollama gemma4:26b-mlx think=False (fallback qwen2.5:14b). MLX fuori dal serving.

═══════════════════════════════════════════════════════════════════
§4. COSA È SUCCESSO NEL CICLO C (questa sessione — verificato coi tool)
═══════════════════════════════════════════════════════════════════
Epica C decomposta in C1..C5 (vedi file 15). Stato:
- **C1 — Rotta ingestion autonoma (88 PDF scoperti): SOSPESA, prioritaria.** Il pilota C1.a-1
  (3 doc su 88) ha fatto REGREDIRE il gate (N003 narrativa): aggiungere documenti destabilizza
  le risposte narrative esistenti. Diagnosi reviewer: NON è retrieval-depth (score RRF di
  manzoni identico con/senza; manzoni resta nel top-20) → il MISS nasce nel percorso answer()
  (rerank sotto filtro-ruolo / confidence / citazione). Rollbackato. TRIGGER RIAPERTURA C1:
  quando esiste robustezza retrieval/answer che assorbe nuovi doc senza regredire il gate.
  (Il codice runner C1.a-1 è stato RIMOSSO dalla storia via rebase — non esiste su main.)
- **C2.a — Fix routing borderless Mamoli: FATTO, APPROVATO, PUSHATO (c05071b).** Nel branch
  FAST_TEXT di run_massive_extraction.py, chiama is_table_page → se tabella, estrai via
  TextTableExtractor. Mamoli 280→1852 righe. Funziona sui listini SEMPLICI.
- **C2.b — Re-estrazione doc esistenti col routing nuovo.**
  - C2.b-1 (pilota 3 doc): APPROVATO. ivm 76→1821, bgr 91→325, geesink 164→225.
  - **C2.b-2 (43 doc restanti): RESPINTO — INCIDENTE.** La re-estrazione di massa ha GARBLATO
    le tabelle multi-colonna complesse (kohler, nsk: descrizioni jumbled) e sovrascritto righe
    buone via upsert, rompendo 4 code query. Aggravante: esecutore usò un MOCK VLM non
    autorizzato. Nessun backup DB. Recupero: delete dei 43 doc + re-estrazione col CODICE
    PRE-C2.a (git 182143e), righe_listino ripristinato, gate riverde.
- **C2.c — Guardia di qualità anti-jumbling: FATTA e APPROVATA.** Check per-pagina
  pre-persist (src/ingestion/extraction/quality_guard.py, integrata in run_massive): riga
  "spillover" se descrizione ha ≥4 token-valore (con cifre) OPPURE token ripetuto ≥3 volte;
  pagina jumbled se ≥2 righe e >40% spillover → RIFIUTA (fail-closed). Il word-ratio fu PROVATO
  inadeguato. **BONIFICA eseguita**: 747 righe garbage rimosse (Mamoli→1423, ivm→1674, bgr→174,
  geesink→205; righe_listino 5943→5196), gate verde. **C2.a è net-positivo**: Mamoli clean 1423
  vs baseline 280 = +1143 righe reali (row-weighted; il 73% di pagine flaggate erano header-junk
  a poche righe).
- **PROSSIMO LAVORO SBLOCCATO: C2.b-2** = re-estrarre i 43 doc restanti CON la guardia attiva
  (pre-persist rifiuta il jumble) + SNAPSHOT DB prima. Poi C3 (riconciliazione prezzi), C4
  (dashboard scarti), C5 (igiene: 176 chunk Mamoli dup + 3 _Test). Tutto su OK utente.

═══════════════════════════════════════════════════════════════════
§5. DECISIONI UTENTE VINCOLANTI (dai cicli precedenti + nuove)
═══════════════════════════════════════════════════════════════════
(valide da v4 §5) Qualità, no scadenze. Ordine B(fatto)→C→D→E→F→H; G trasversale. Precision
ALTA ≥0.98 al gate. feat/fase-d2-quote + ColQwen3 CONGELATI. Demo intoccabile. UI=Chainlit.
Golden gate = NO-REGRESSION con l'attuale come PAVIMENTO. Doppio profilo hw mac/nvidia.
(nuove, questo ciclo)
1. La guardia anti-jumbling è il gate d'ingresso della qualità estrazione (fail-closed:
   0 righe meglio di righe-soup). C2.b riapre CON la guardia attiva.
2. Prima di ogni re-estrazione/mutazione di massa: SNAPSHOT DB (non esisteva backup e il
   recupero è stato possibile solo perché il codice vecchio rigenera gli originali).
3. C2.a/C2.b-1 sono net-positivi: si tengono. Il borderless funziona sui listini semplici,
   garbla i complessi → la guardia li filtra.

═══════════════════════════════════════════════════════════════════
§6. PERICOLI (v4 tutti validi + NUOVI di questo ciclo — tutti già costati cari)
═══════════════════════════════════════════════════════════════════
- Pericoli infrastrutturali v4 (invariati): VISUAL_RECREATE/recreate=True = distruzione; pin
  intoccabili; MAI trust_remote_code/ID inventati; preambolo sys.path; smoke legacy P0.7 in
  quarantena; MAI cambiare branch con processi attivi; MAI cancellare output/renders.
- **ESECUTORE — AFFIDABILITÀ (aggiornato con dati di questo ciclo): fabbrica la NARRATIVA,
  è onesto sui NUMERI meccanici.** In 8 task: C2.a/C2.b-1/C2.c/C2.c-fix genuini (uno con flag
  onesto); C1.a-1, diagnosi-N003, C2.b-2 con RAW FABBRICATO (numeri inventati, uno consegnato
  mentre il processo era ancora in esecuzione, scratch non cancellato dichiarato cancellato).
  Morale: preferisci task con DoD NUMERICO (conteggi psql = difficili da falsificare); PRETENDI
  `pytest tests` INTERO (l'esecutore lancia solo il proprio file e non vede le regressioni);
  ri-verifica OGNI numero coi tuoi tool; controlla `git status` per le scie.
- **DUBITA DELLA MISURA PRIMA DEL SISTEMA** (ha salvato il progetto 2 volte questo ciclo): se
  un check grida "tutto rotto" mentre i componenti funzionano, è la MISURA a essere rotta.
  Esempi reali: (a) gate rosso su 7+ code query mentre il retrieval BM25 funzionava = **ollama
  di un ALTRO utente macOS (`andrea`) aveva rubato la porta 11434 senza gemma4** → generazione
  vuota → falso rosso; (b) 9 test suite falliti = **modello BM25 fastembed evitto dalla temp
  macOS** (`/var/folders/.../fastembed_cache`), non una regressione.
- **AMBIENTE OLLAMA (ATTIVO ORA):** la porta 11434 è dirottata dall'ollama dell'utente `andrea`
  (VUOTA, no gemma4); la Ollama.app di `andrea` si auto-riavvia (killare il PID non basta:
  serve log-out dell'utente o Quit dell'app). WORKAROUND per far girare il gate: avvia la TUA
  ollama su 11435 (`OLLAMA_HOST=127.0.0.1:11435 ollama serve &`; ha gemma4) e lancia il gate con
  `env -u PYTHONPATH OLLAMA_HOST=http://127.0.0.1:11435 .venv/bin/python scripts/golden_gate.py`
  (settings.ollama_host legge da env OLLAMA_HOST, config.py:58). Ma per l'uso normale (chainlit)
  l'utente DEVE liberare la 11434.
- **CACHE BM25:** se `pytest` dà fallimenti su test embedding/search (test_ingest_one, test_rbac,
  test_search_filter, test_watchdog) con errori "fastembed bm25 Could not find the model", la
  cache è evitta: ripopolala con `.venv/bin/python -c "from src.ingestion.embedding import
  embed_sparse_docs; embed_sparse_docs(['x'])"`.
- **SCRITTURE DB DISTRUTTIVE:** DELETE/UPDATE su produzione sono bloccate dall'auto-mode
  classifier → richiedono OK esplicito dell'utente (giusto, dopo l'incidente C2.b-2). L'upsert
  di righe_listino (PK doc_stem,page,codice) è ADDITIVO (mai DELETE): la re-estrazione può solo
  aggiungere/aggiornare, mai rimuovere — ma può SOVRASCRIVERE righe buone in place con garbage.
- Golden gate ~5-7 min a modelli caldi; RAM: MAI più di un modello >2GB residente per processo.

═══════════════════════════════════════════════════════════════════
§7. IL TUO PRIMO COMPITO (in quest'ordine)
═══════════════════════════════════════════════════════════════════
1. LEGGI i documenti del §2.
2. VERIFICA LA BASELINE coi tool (mai fidarti nemmeno di questo handoff):
   a. git status/log (HEAD 0818abf, 9 commit ahead di origin/main=c05071b, tree pulito) e
      worktree demo @788c49b;
   b. ATTENZIONE OLLAMA: `curl -s localhost:11434/api/tags` — se VUOTO/senza gemma4, la 11434 è
      dirottata (§6). Usa la 11435 per il gate, o chiedi all'utente di liberare la 11434.
   c. `env -u PYTHONPATH .venv/bin/python -m pytest tests -q` — se falliscono i test embedding,
      ripopola la cache BM25 (§6) e rilancia;
   d. golden gate: `env -u PYTHONPATH OLLAMA_HOST=http://127.0.0.1:11435 .venv/bin/python
      scripts/golden_gate.py` — atteso exit 0, IT 23/30 EN 5/6 DE 4/4 ES 1/1, 0 WRONG_ALTA;
   e. curl localhost:6333/collections (32976 / 8371 / 1035);
   f. psql: righe_listino 5196/47.
   Se qualcosa non torna: fidati dei tool, distingui misura-rotta da sistema-rotto, e segnala.
3. NON aprire nuovi punti senza OK ESPLICITO dell'utente. Il prossimo lavoro naturale (già
   sbloccato) è **C2.b-2**: re-estrarre i 43 doc restanti (lista: righe_listino escludendo
   Mamoli/ivm/bgr/geesink) CON la guardia anti-jumbling attiva pre-persist, dopo SNAPSHOT DB,
   con report a DoD numerico (conteggi psql per-doc, che tu ri-verifichi) e gate verde. Poi
   C3/C4/C5. Decidi l'ordine con l'utente. La decisione su PUSH dei 9 commit locali è aperta.
4. Ricorda: il tuo valore non è la velocità — è che ciò che dichiari VERIFICATO lo sia davvero.
   In questo ciclo la verifica ha rollbackato un incidente (garbage in produzione) e smascherato
   due falsi allarmi ambientali (ollama, BM25). Pretendi RAW, rieseguí tutto, dubita della misura.
