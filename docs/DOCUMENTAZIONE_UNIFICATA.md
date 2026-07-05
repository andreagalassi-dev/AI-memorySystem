# Documentazione Unificata del Sistema AI-memorySystem

Questo documento riassume e unifica tutte le specifiche, metriche, handoff e design doc storici presenti nella cartella `docs/` e `docs/superpowers/specs/`. Il file `SPEC.md` non è stato alterato o incluso come da direttiva.

---

## 1. Fase 0 & Ingestion (Pipeline e Sfide)
*(Rif: `FASE_0_SUMMARY.md`, `ingestion-challenges.md`, `ingestion-pipeline-attuale.md`, `p26-ingestion.md`)*

- **Sfide dell'Ingestion**: Il parsing di PDF complessi industriali (tabelle malformate, esplosi senza testo testabile, manuali in multilingua) è stato il problema cardine. Strumenti come Tika o pdfplumber fallivano su layout complessi, mescolando colonne.
- **Pipeline Attuale**: L'ingestion usa **Docling** (v2) supportato dall'OCR vision-language (ColPali per le immagini e Qwen2-VL). L'ingestion è strutturata su Postgres (come Single Source of Truth) e Qdrant (per il Vettoriale).
- **Chunking**: Si basa su paragrafi o righe di tabelle (`chunk_table`, `chunk_page`). Ogni chunk ha un set esteso di metadati (es. `code_normalized`, `lang`, `doc_stem`).

## 2. Profili Brand e Supporto Multi-Brand
*(Rif: `profili-brand-copertura.md`, `multi-brand-eval.md`)*

- **Configurazione**: I codici cambiano sintassi tra i brand (es. `V0...` per Mamoli, `0US...` per Electrolux). I profili di Ingestion (tabella `ingestion_profiles`) gestiscono regex per estrarre e validare questi codici testuali duranti l'OCR.
- **Generico e Sicurezza**: Se un PDF appartiene a un brand ignoto, si usa un profilo `generic`. I risultati ottenuti tramite fallback "generico" vedono la loro confidenza bloccata e cappata (livello MEDIA massimo), garantendo che l'utente non riceva mai un grado di affidabilità "ALTA" su codici non validati a priori dalle regex.

## 3. Fase P1 (Motore Lessicale e Tabelle)
*(Rif: `p1-candidate-tables.md`, `p1-gate-decision.md`, `p1-metrics.md`)*

- **Focus**: Il recupero *esatto* dei codici alfanumerici (Retrieval sparso / BM25 tramite Qdrant Sparse Vectors con SPLADE/BGE-m3).
- **Decisioni del Gate**: È stato deciso di non usare modelli linguistici in questa fase per garantire tempi di risposta fulminei (≤ 50ms) e score trasparenti sul recupero dei codici riga-su-riga.
- **Metriche P1**: I test sul set avversario "Mamoli" hanno dimostrato R@1 altissimo per le chiavi testuali dirette, isolando le debolezze unicamente nelle query in linguaggio naturale puro.

## 4. Fase P2 (Retrieval Ibrido, Visivo e Recall Globale)
*(Rif: `p2-handoff.md`, `p2-metrics.md`, `p27-visual.md`, `p28-global-recall.md`)*

- **Ibrido e Router**: Introdotto un router `routed_rr` (dense + sparse). Le query in linguaggio naturale puro o misto vengono instradate anche sul vettore denso.
- **ColPali (Visione)**: Il recupero grafico degli "esplosi" muti usa un embedding visivo multi-vettore `ColPali` (confronto tramite MaxSim). Questo copre il buco nero dei manuali grafici privi di descrizioni testuali.
- **Reranker**: Adottato `BGE-reranker-v2-m3` come filtro cross-encoder per i top-20 risultati, migliorando il Mean Reciprocal Rank (MRR) di oltre +0.09 su query cross-lingua complesse.
- **Classificatore Pagine**: Determina automaticamente se una pagina è grafica o testuale prima dell'ingestion (tramite analisi dei pixel/char), indirizzando le pagine grafiche all'embedder ColPali.

## 5. Fase P3 (Grafo, LLM, "Non lo so" e Guardrail)
*(Rif: `p3-handoff.md`, `p3-to-p4-handoff.md`, `p32/p33/p34/p35`, `docs/superpowers/specs/*`)*

- **Grafo Deterministico (LangGraph)**: L'orchestrazione è governata da una Macchina a Stati Finita (FSM). L'unico step non-deterministico è la chiamata LLM (Sintesi), incapsulata in rigidi vincoli di input/output.
- **Il Principio del "Non lo so" (P3.2)**: Calcolatore di confidenza che sfrutta il margine tra il 1° e il 2° risultato. Se il retrieval è scarso (livello BASSA), l'LLM non viene neanche invocato: il sistema rifiuta a priori.
- **Diagnostica Multi-canale (Fusion P3.3)**: Valuta l'accordo tra il recupero testuale e visivo, per capire se indicano la stessa pagina o si contraddicono.
- **Sintesi e LLM Spike (P3.4)**: È stato scelto e validato `qwen2.5:14b` come motore di sintesi locale (Ollama). Costretto a citare la fonte `(doc, pag. N)`.
- **Guardrail Deterministico (P3.1)**: Validazione post-Sintesi. Se l'LLM menziona un codice che *non* era presente nei documenti del contesto ritornato da Qdrant, il Guardrail emette un `BLOCK` e annulla l'affidabilità della risposta, evitando allucinazioni (esposte come "Verifica manuale").

## 6. Fase P4 (Postgres SSOT, RBAC e Versioning)
*(Rif: `p4-handoff.md`)*

- **Architettura Master-Slave**: PostgreSQL diventa l'unica *Single Source of Truth* per versioni file, ruoli e cataloghi. Qdrant è una sua mera proiezione.
- **RBAC (Ruoli)**: Tre ruoli base definiti (`tecnico`, `commerciale`, `amministrazione`). Le ricerche sono strettamente confinate al ruolo chiamante tramite un filtro `AND` obbligatorio propagato fino a Qdrant (`ruoli_consentiti`).
- **Versioning (Superseding)**: L'aggiornamento di un manuale segna la V1 come `superseded` e la V2 come `current`. Se l'evidenza per una risposta proviene solo da documenti `superseded`, il sistema aggiunge deterministicamente in coda un `"ATTENZIONE: Risposta basata su documento obsoleto"`.
- **Fail-Closed di Rete**: Se PostgreSQL, Qdrant o Ollama sono irraggiungibili, il grafo LangGraph crolla su un gracefully fall "Sistema non disponibile" (livello BASSA) e traccia il log, impedendo la rottura.

## 7. Fase Auto-Ingestion (Watchdog)
*(Rif: `watchdog-e2e.md`)*

- **Il Demone**: Sviluppato un Watchdog basato su `psycopg3` che monitora l'aggiunta, modifica e rimozione di PDF. Interagisce con PostgreSQL.
- **Sync**: Se un file viene rimosso, viene applicato un *Soft Delete* archiviante propagato a Qdrant, per evitare che risultati appartenenti a documenti rimossi continuino a riaffiorare e ad alimentare le sintesi dell'LLM.
