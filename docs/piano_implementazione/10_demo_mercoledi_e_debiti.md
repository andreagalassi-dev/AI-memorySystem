# Demo di mercoledì — piano operativo + registro COMPLETO dei tagli

> **Nota di allineamento (25/08/2026):** registro storico della demo del 15/07/2026 (CLAUDE.md
> lo classifica tra i documenti storici, non toccare il contenuto sotto). Le menzioni sotto a
> Streamlit e a Ollama `qwen2.5:14b` come cervello di produzione erano corrette per quella
> demo; oggi la UI di prodotto è **Chainlit** e il cervello di produzione è **Gemma 4 26B via
> Ollama** (fallback `qwen2.5:14b`) — vedi CLAUDE.md. La collezione Qdrant `aike_chunks` citata
> alla riga 138 (dipendenze del worktree demo permanente) era corretta all'epoca; il
> 24/08/2026 la produzione principale è passata a `aike_chunks_v2` — il worktree demo, se e
> quando riattivato, va verificato per capire quale collezione stia davvero interrogando.

> **Contesto:** deadline demo presentabile **mercoledì** (~2,5 giorni dal 2026-07-12).
> Per arrivarci si fanno tagli **temporanei** sulle prestazioni/completezza del prodotto.
> Questo file è il registro vincolante: OGNI taglio è tracciato con il perché e come
> recuperarlo. Dopo la demo si riparte da qui e da `08_piano_correzione.md` (che resta
> il piano madre — questo file lo integra, non lo sostituisce).

---

# PARTE 1 — Percorso verso la demo (in ordine di esecuzione)

## D1 — Rete di sicurezza: chat funzionante su stack collaudato (OGGI)
La base demo è `main` (P2–P5 collaudati: retrieval ibrido BGE-M3+BM25, guardrail codici,
confidence calibrata, sinonimi, RBAC, versioning) + `p5-ui` (chat Streamlit verificata e2e
a P5: badge sicurezza, citazione pagina, render, feedback strutturato).

1. `ollama pull qwen2.5:14b` (~15 min) — il grafo di main genera con questo. NB: per la
   demo si usa Ollama/qwen2.5 (fluido), NON il cervello MLX Gemma 26B (risposte a decine
   di secondi su M1 Pro — vietato in demo live, vedi taglio T8).
2. Branch demo dedicato: `demo-mercoledi` da `main`; rebase/merge di `p5-ui` (2 commit).
   Risolvere i conflitti SENZA toccare la logica core.
3. Smoke manuale della chat: (a) query codice → risposta+fonte+badge; (b) query naturale
   IT → risposta o "non lo so"; (c) render pagina visibile; (d) avviso obsoleto.
4. Se qualcosa è rotto: fixare SOLO ciò che serve alla demo, annotare il resto in Parte 2.
5. **Checkpoint fine giornata: demo minima ESISTE.** Da qui si può solo migliorare.

## D2 — Innesto del visivo nuovo (LUNEDÌ, solo se l'eval passa)
Dipende dall'esito dell'ingestion+eval ColQwen3 in corso (`aike_pages_visual_colqwen3`,
soglia recall@5 ≥ 0.875 nella migliore delle 4 configurazioni).
1. Se PASSA: promozione della collection nuova + aggancio del ramo visivo alla UI demo
   (query "mostrami l'esploso di X" → immagine pagina nel pannello). È il wow-effect
   principale per il manifatturiero.
2. Se NON passa: si usa il percorso single-vector esistente (0.733) con query curate
   (in demo le domande le scegliamo noi → tutte verdi). Annotare in Parte 2 (T1).
3. In parallelo (runtime macchina, non blocca): lancio estrazione 0.3
   `scripts/run_full_corpus.py` in background con log → popola `estrazione.righe_listino`.

## D3 — Innesto prezzi + curazione demo (MARTEDÌ)
1. Se `righe_listino` è popolata e i campioni verificati (codici verbatim vs PDF):
   innesto lookup STRUCTURED nella demo (query codice → prezzo esatto ancorato con fonte).
   Se i dati non convincono: NON innestare (mai sbagliato in silenzio > feature in più).
2. **Curazione dello script demo** — 6-8 query provate N volte, che coprono:
   - 2 query codice (guardrail + fonte esatta);
   - 2 query visive (esploso renderizzato);
   - 2 query naturali IT (risposta ancorata);
   - 1 query fuori corpus → "non lo so" (VENDERLO come feature: il sistema non inventa);
   - (se D3.1 ok) 1 query prezzo.
3. Piano B anti-imprevisti: screenshot + screen-recording di OGNI flusso riuscito.
   Se mercoledì un servizio muore, si presenta comunque.
4. Verifica operativa: docker up, ollama attivo, modelli in cache, niente download live.

## D4 — MERCOLEDÌ mattina: FREEZE
Nessun commit, nessun aggiornamento, solo prova generale con lo script demo.

---

# PARTE 2 — REGISTRO DEI TAGLI (debiti da saldare DOPO la demo)

> Regola: nessuna voce si chiude senza prova osservata + verdetto reviewer.

## T1 — Retrieval visivo: decisione finale primo stadio ⚠️ IL PIÙ IMPORTANTE
- **Stato:** ColQwen3 (TomoroAI vendored, multivettore 320d page-level) in ingestion su
  collection parallela `aike_pages_visual_colqwen3`; eval 4 configurazioni pendente.
  Pipeline single-vector (Qwen3-VL-Embedding-2B, 1024d MRL) resta attiva a 0.733.
- **Da fare:** eval completa per lingua → se ≥0.875 promozione a produzione (switch in
  qwen3vl.py/pixelrag + E2E grafo congelato); se sotto, decidere con i numeri (opzioni
  residue: 2048d, 8B, tile-level multivettore, accettare). Smontare il percorso perdente
  SOLO dopo la promozione (mai prima).
- **Nota:** riportare quale documento era il falso positivo "Altro2" nel sanity Kohler.

## T2 — Punto 0.3: estrazione massiva → righe_listino (ramo STRUCTURED)
- **Stato:** `righe_listino` ha tentato un'estrazione best-effort per la demo. **ESITO FALLITO**: l'estrazione non è verbatim. Mamoli p230-231 sono completamente assenti dal DB (max page 185) e le righe presenti contengono testo concatenato male e allucinazioni. L'innesto del lookup prezzi in UI (D3) è stato **cancellato** ("mai sbagliato in silenzio").
- **Da fare (completo, post-demo):** run completo con verifica campioni manuali
  (Mamoli p230-231 verbatim vs PDF), censimento scarti con motivo, riconciliazione
  Fase 2 (`python -m src.merge.reconcile --dry-run` → numeri linked/orphan/ambiguous),
  query STRUCTURED e2e con prezzo ancorato. DoD in 08_piano_correzione §0.3. Rivedere prompt e pipeline OCR per risolvere le allucinazioni.

## T3 — Punto 0.4: fine dei fail-safe silenziosi
- `pipeline.py::_default_deps`: ogni `except Exception: dep=None` deve loggare warning
  strutturato (ramo, eccezione); `/health` esteso con stato rami (structured/visual/
  narrative + modello residente). Test con caplog. NON fatto, tagliato per la demo.

## T4 — Integrazione vera (Priorità 1 del piano)
- Merge `feature/fase1-model-multiplexer` → `main` (la demo gira su un branch ibrido
  `demo-mercoledi` che NON è l'integrazione vera).
- Cancellazione ~16 branch già mergiati; decisione su `feat/fase-d2-quote` (bozza
  preventivo, 3 commit orfani).
- UI ricollegata al grafo TRI-INTENT completo (la demo usa il grafo di main, vecchio:
  niente sql_node/visual_node nuovi) + pool connessioni Postgres RO
  (TODO a pipeline.py:623 — oggi 1 connessione per answer(), leak sotto uso continuo).
- Smoke e2e (`scripts/smoke_e2e.py`, 3 query una per ramo, exit≠0 se un ramo degrada)
  come gate pre-merge documentato.

## T5 — Cervello MLX in produzione
- La demo usa Ollama qwen2.5:14b (T8). Post-demo: decidere il default di produzione tra
  MLX Gemma 26B (qualità, lento su M1 Pro) e Ollama gemma4:26b-a4b-it-q4 (da scaricare:
  `ollama pull gemma4:26b-a4b-it-q4` — MAI fatto: oggi il fallback punta a un modello
  NON installato → se MLX fallisce, generazione morta).
- Valutare `enable_thinking` (oggi False) vs qualità risposte; misurare latenza reale
  per query su entrambi i backend; budget max_tokens per il render.

## T6 — Qualità retrieval testuale (Priorità 2)
- Golden dataset come gate automatico anti-regressione (comando unico, baseline salvata,
  fail su regressione, metriche PER LINGUA). Oggi non esiste alcun gate.
- Ranking naturali: R@1 0.486 fermo (collo reranker, noto da P2). A/B: reranker più
  forte / rerank LLM top-20 / promozione fusion ricalibrata. Vincolo: mai reranking
  sulle query code, precision ALTA=1.0 intoccabile.
- Eval visiva: il set ha SOLO 15 query (1 ES anomala in un corpus IT/EN/DE — verificare
  espansione o correzione del set); differenze <2 hit = rumore. Va ampliato (il feedback
  UI serve anche a questo).

## T7 — Debiti tecnici del percorso visivo nuovo
- **Vendor TomoroAI:** 3 patch locali (ClassVar sub_configs; tie_weights firma+namespace;
  mm_token_type_ids forward+processor). Sorvegliare gli aggiornamenti upstream del repo
  HF: se fixano, sostituire il vendor con revisione nuova pinnata. Licenza e provenienza
  annotate nei file.
- **Latenza reranker:** 60 candidati × ~1s/immagine su MPS = decine di secondi/query.
  Per la produzione: profilare, valutare pool adattivo (30 se margine alto, 60 se basso),
  batch delle immagini nel predict.
- **Render riusati dal reranker:** usa i thumb PNG salvati dall'ingestion single-vector
  (output/visual/renders). Se si smonta quella pipeline, i render vanno generati dal
  percorso nuovo (oggi lo script colqwen3 li risalva — verificare copertura completa).
- **max_resident=2** hardcoded nel setup visivo: rivedere quando MLX Gemma e i modelli
  visivi convivono (26B=14GB + 4.5B=9GB + reranker 4GB > 32GB → serve orchestrazione
  multiplexer reale a livello di processo, non per-script).
- **Igiene MPS**: empty_cache periodico presente negli script ingestion; verificarla nel
  serving continuo (UI aperta ore).

## T8 — Scelte demo da NON dimenticare di revertire
- La demo gira su qwen2.5:14b via Ollama: NON è lo stack di destinazione (Gemma/Nemotron).
- Branch `demo-mercoledi`: throwaway — niente merge diretto su main; il lavoro vero
  passa da T4.
- Eventuali fix "solo demo" fatti in D1.4 vanno riportati/rifatti puliti sul branch vero.
- Il worktree demo symlinka output/visual/renders del repo principale: i render on-demand generati dalla UI demo scrivono nella directory di produzione (additivo). Rimuovere il symlink insieme al worktree.
- app.py demo contiene un input di test nascosto attivato da `PYTEST_CURRENT_TEST` (shim per la suite del worktree dopo il passaggio a chat_input): hack demo-only, NON portarlo nel branch vero.
- I PDF del corpus sono symlinkati PIATTI in `corpus/` del worktree demo (fix reviewer 14/07: il render on-demand delle pagine citate falliva in silenzio perché corpus/ era vuoto e `glob("**")` non attraversa dir symlinkate). Ambientale, gitignorato: rimuovere insieme al worktree.
- **DEMO ESEGUITA CON SUCCESSO il 15/07.** Decisione utente: il worktree `../AI-memorySystem-demo` (branch `demo-mercoledi`, HEAD `788c49b`) resta come **demo permanente** — NON rimuoverlo, NON cancellare il branch. Dipendenze vive da preservare: collections Qdrant `aike_chunks` e `aike_pages_visual`, Postgres `aike_registry`, Ollama `qwen2.5:14b`, `.venv` + `corpus/` + `output/visual/renders/` del repo principale (symlinkati). ⚠️ Re-ingestion o modifiche future alle collections di produzione possono cambiare il comportamento della demo: ricollaudare le query del file 14 dopo interventi sugli indici. Il vincolo "throwaway, mai mergiarlo su main" resta valido.
- Tarature demo-only in app.py: pool visivo ridotto (`tile_k=75`, ~15 pagine al reranker, smoke 4/4 verde riverificato dal reviewer) + cache dei risultati visivi per (query, ruolo). In produzione la taratura del pool va rifatta con l'eval completa (T7 latenza reranker).

## T9 — Igiene di sistema (Priorità 3 del piano madre, invariata)
- ruff pulito su tutto il repo (erano 51 errori; i file nuovi sono puliti, il pregresso no).
- Unificare i piani: PIANO-IMPLEMENTAZIONE.md → indice verso docs/piano_implementazione/.
- README ("Stato: P0 in corso" — falso) e CLAUDE.md (stack: dice Marker/BGE/Qwen3 — va
  allineato a: Docling+TextTableExtractor+GLM-OCR fallback, Gemma/Nemotron via MLX,
  ColQwen3/Qwen3-VL per il visivo, reranker Qwen3-VL).
- `pyproject`: streamlit nel core ma usato solo dalla UI (extra `ui` o merge della UI).
- docker-compose: container `ocr` e `ui` vuoti (backend = solo /health).
- Payload `doc` vs `doc_stem` mai uniformato (nota B1 in ingestion-pipeline-attuale.md).
- Archiviare in scripts/archive/ gli spike superati; il chunker non indicizza ancora i
  codici riparati dal debt §4 (nota storica in memoria).
- Fase 4 (Chainlit), Fase 3 (fallback triplo livello + web agent), Fase 5 (memoria),
  Fase 6 (ERP): tutte DOPO, ordine in PIANO-IMPLEMENTAZIONE.

---

# PARTE 3 — Fotografia dello stato VERIFICATO (2026-07-12)

| Componente | Stato | Prova |
|---|---|---|
| Suite test | ✅ 687 passed, 2 skipped | rerun reviewer |
| Cervello MLX (0.1) | ✅ chiuso | Gemma 26B genera pulito, needs_mlx verde |
| Ambiente librerie | ✅ bonificato | mlx-lm 0.31.3 pin, transformers 5.12.1, peft 0.19.1 |
| Visivo single-vector | ✅ operativo, sotto soglia | recall@5 0.733 (IT 0.600) |
| Visivo ColQwen3 | ⏳ ingestion in corso | collection parallela, config multivettore verificata |
| righe_listino | ❌ vuota | 0 righe (T2) |
| UI | ⚠️ solo su p5-ui, grafo vecchio | da riagganciare (D1/T4) |
| main | ⚠️ 60+ commit indietro | T4 |
