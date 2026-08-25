# HANDOFF REVIEWER — stato completo dell'orchestrazione (2026-07-13 sera)

> **Nota di allineamento (25/08/2026):** questo è un resoconto storico, fotografia del
> 13/07/2026 — non lo stato attuale. In particolare: la collezione Qdrant `aike_chunks`
> citata sotto era corretta allora; il 24/08/2026 la produzione è passata a
> `aike_chunks_v2` (`aike_chunks` resta solo come rollback). La UI era Streamlit e il
> modello demo `qwen2.5:14b`: oggi la UI di prodotto è **Chainlit** e il cervello di
> produzione è **Gemma 4 26B via Ollama** (fallback `qwen2.5:14b`) — vedi CLAUDE.md.

> **Chi legge questo file:** l'istanza Claude (reviewer/orchestratore) che continua il
> lavoro in una nuova chat. Contiene TUTTA la conoscenza operativa della sessione
> precedente. Le dimenticanze qui possono causare errori fatali sul repository:
> leggere per intero prima di qualunque azione.

## 1. RUOLO E METODO (non negoziabili)

- **Io (Claude) = REVIEWER/ORCHESTRATORE.** Un agente AI esecutore (istanza separata,
  senza il mio contesto) fa il lavoro; l'utente fa da tramite copiando i miei prompt
  all'agente e incollandomi i suoi report. Io NON implemento le feature: verifico,
  emetto verdetti, scrivo il prossimo prompt.
- **VERIFICARE OGNI CLAIM con i tool prima del verdetto.** Gli agenti hanno già:
  dichiarato test su un modello diverso da quello di produzione, dichiarato "ruff
  pulito" con 4 errori, dichiarato committato ciò che non lo era, rimosso check che
  protestavano (answerability) invece di indagare, rotto l'ambiente inseguendo un
  modello. MAI fidarsi del report: git log, pytest, psql, curl su Qdrant, letture di
  codice — sempre.
- **Formato dei miei prompt all'agente:** diretti, numerati, con evidenze citate
  ("verificato dal reviewer: ..."), DoD espliciti, STOP obbligatori con report a
  output incollati. Un task alla volta.
- L'utente parla italiano; prompt e risposte in italiano.

## 2. REGOLE APPRESE (ognuna è costata cara)

1. **Librerie condivise INTOCCABILI** senza autorizzazione: transformers 5.12.1,
   torch 2.13.0, peft 0.19.1, mlx-lm>=0.31.3 (PINNATO in pyproject extra mlx),
   sentence-transformers. Un agente ha retrocesso mlx-lm rompendo il cervello Gemma:
   qualunque modello richieda versioni diverse è RESPINTO di default.
2. **Checkpoint HF:** verificare model_type/classe/compatibilità con lo stack PRIMA
   del download; MAI trust_remote_code (vendorizzare); MAI ID inventati.
3. **MAI cambiare branch nella working dir con processi attivi** → worktree separati.
   Worktree demo: `../AI-memorySystem-demo` (branch `demo-mercoledi`, THROWAWAY,
   mai mergiarlo su main).
4. **MAI più di un modello >2GB residente per processo** (OOM ha già spento il Mac).
   Sequenziare le fasi, `torch.mps.empty_cache()` tra i passaggi, preflight prima dei
   run pesanti (`scripts/index_colqwen3.py` lo ha integrato: python>1GB e `ollama ps`).
5. **Gli indici vettoriali si validano CROSS-PROCESS** (gate in-process = ingannevole,
   visto col bug della proiezione random). Gate: `scripts/run_gate_colqwen3.py`.
6. **MAI zittire un check che protesta** (answerability, guardrail, gate): si indaga.
7. **Ingestion non-distruttive**: collection parallele, resume non-distruttivo
   (decide_recreate), round-trip check, mai `recreate` incondizionato.
8. **Ogni spike lascia un artefatto committato** in scripts/; niente file alla root;
   gli agenti lasciano scie di file temporanei — controllare `git status` a ogni review.
9. Prima di run lunghi: stimare i tempi con dati misurati e fare pre-test economici
   (mini-eval in-memory prima di ingestion; gate incrementali "si paga dopo le prove").
10. Il conteggio patch sul vendor TomoroAI è 4 (ClassVar sub_configs; tie_weights
    firma+namespace; mm_token_type_ids forward+processor; embedding_proj_layer load
    manuale + guardia missing_keys=={vlm.lm_head.weight}). Soglia fork superata:
    una QUINTA patch = si cambia candidato senza appello.

## 3. STATO DEL SISTEMA (tutto verificato con tool, 13/07 sera)

### Infrastruttura
- Repo: `/Users/AndreaGalassi/Documents/AI/AI-memorySystem`, branch
  `feature/fase1-model-multiplexer`. Worktree demo: `../AI-memorySystem-demo`.
- Servizi: docker `aike_postgres` (db **aike_registry**, user aike) e `aike_qdrant`
  (localhost:6333). UI demo Streamlit su :8501 (dal worktree).
- Ambiente: `source .venv/bin/activate`; suite `python -m pytest tests -q`
  (~687 passed, 2-3 skipped); Python 3.11; Mac M1 Pro 32GB.
- Qdrant collections: `aike_chunks` (32.976 punti testo BGE-M3+BM25),
  `aike_pages_visual` (8.371 punti tile-level single-vector 1024d = PRODUZIONE visiva),
  `aike_pages_visual_colqwen3` (1.035 punti multivettore 320d = candidato CONGELATO).
- Ollama: server attivo, modello demo `qwen2.5:14b` scaricato. MLX cache: Gemma 26B.

### Punti del piano di correzione (docs/piano_implementazione/08)
- **0.1 CHIUSO**: cervello = MLXModel su Port LanguageModel,
  `mlx-community/gemma-4-26b-a4b-it-4bit` via multiplexer, `enable_thinking=False` +
  parsing canale `<|channel>thought` come rete di sicurezza; FallbackLLM → OllamaModel
  loggato; synthesis migrata al Port; config: `ollama_model="gemma4:26b-a4b-it-q4"`
  (NON ancora scaricato — T5 nel registro).
- **0.2 DECISO (T1)**: dopo 3 eval invalidate (aggregazione tile mancante; indice
  corrotto; proiezione random) e la 4ª finalmente valida: **ColQwen3-Tomoro CONGELATO**
  (puro 0.667 / +exp 0.533 < gate 0.72; EN 1.000, IT 0.500 — gap linguistico IT).
  **Produzione visiva = single-vector Qwen3-VL-Embedding-2B (1024d MRL,
  prompt_name="query") + Qwen3VLReranker 2B = recall@5 0.733** (IT 0.600). Espansione
  IT→EN (dizionario, src/retrieval/qwen3vl.py + expansion) attiva. Q15
  (volano/Flywheel) verificata A OCCHIO dal reviewer: GT giusto, set eval SANO,
  fallimento linguistico reale.
- **0.3 FATTO (run demo)**: `estrazione.righe_listino` = **2331 righe / 47 doc**
  (verificato psql), 0 scarti, codici verbatim (Mamoli C/G intatte). ⚠️ Copertura
  sospetta: Mamoli solo 280 righe su ~100 pagine di listino → audit post-demo (T2).
  Riconciliazione Fase 2: 11 linked / 2320 orfani tutti PAGINA_VISIVA_ASSENTE =
  difetto di DESIGN (pagine listino non stanno nell'indice visivo) → ripensare (T2).
- **0.4 NON FATTO** (log degradazione rami + /health) — registro T3.

### Demo (deadline MERCOLEDÌ 15/07 — piano in docs/piano_implementazione/10)
- D1 FATTO: worktree demo = main + p5-ui; smoke via answer() PASSATO (codice→ALTA
  con fonte e guardrail PASS; naturale→risposta o rifiuto onesto; fuori corpus→rifiuto;
  visiva→degrada con garbo). Demo usa Ollama qwen2.5:14b (MLX troppo lento live).
- Script demo: `docs/piano_implementazione/11_script_demo.md` (nel worktree) con query
  collaudate + cold start + Piano B screenshot.
- **IN CORSO ADESSO (anticipato a oggi)**: D2 = innesto del visivo di PRODUZIONE
  (single-vector+reranker su aike_pages_visual) nella UI demo; query visive curate
  candidate: Q5/Q10/Q12/Q13 (margini forti). Poi prova generale. Mercoledì: FREEZE.

### Pulizia repo PENDENTE (34 file non committati — prompt già dato all'agente)
Commit in ordine: (a) route +2 pagine GT; (b) index_colqwen3+run_gate_colqwen3
(gate cross-process); (c) evaluate pool30 + reranker batch_size; (d) UNO dei test_*.py
rinominato spike_colqwen3_determinism.py; (e) run_mini_eval_colqwen3.py; (f) file 10
(demo+tagli). CANCELLARE: gli altri 24 scripts/test_*.py + output_eval.txt +
output_index.txt dalla root. Verificare git status pulito.

## 4. DOPO LA DEMO (ordine, dal registro T1-T9 nel file 10)
T4 integrazione (merge feature→main, UI sul grafo tri-intent, pool connessioni PG,
smoke e2e gate) → T2 (audit copertura estrazione + redesign riconciliazione) →
T3 (0.4 log degradazione) → T5 (cervello default: MLX vs ollama gemma4, pull mancante)
→ T6 (golden dataset gate + ranking naturali R@1 0.486 + ampliare eval visivo 15 query)
→ T7 (debiti visivi: latenza reranker, renders, max_resident, sorvegliare upstream
Tomoro) → T9 igiene (ruff ~51 errori legacy, piani duplicati, README/CLAUDE.md stack
obsoleto, streamlit dep, container ocr/ui vuoti, doc/doc_stem).
Opzioni future primo stadio visivo: OpenSearch-AI/Ops-Colqwen3-4B (mai provato),
Qwen3-VL-Embedding-8B, 2048d. Il tallone è SEMPRE il cross-lingua IT.

## 5. PERICOLI SPECIFICI PER IL REPO (errori fatali possibili)
- `VISUAL_RECREATE=1` o `recreate=True` sulle collection SBAGLIATE (aike_chunks o
  aike_pages_visual di produzione) = distruzione dati di produzione.
- `uv sync` dopo aver toccato pyproject senza il pin mlx-lm = downgrade silenzioso.
- Merge di `demo-mercoledi` su main = inquinamento della storia (è un ibrido throwaway).
- `git checkout` di branch nella working dir principale con processi/eval attivi.
- Eliminare `output/visual/renders/` (serve a UI e reranker).
- Fidarsi di un gate/eval senza verificarne data, contenuto e processo di origine.
