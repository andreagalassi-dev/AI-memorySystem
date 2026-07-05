# Piano di Implementazione: Verso la Produzione Locale (UI, API e ERP)

Questo documento traccia la roadmap per trasformare l'attuale core AI in un sistema aziendale usabile in locale da utenti non tecnici. Le fasi sono ordinate per dipendenza reale: ogni step si costruisce sui risultati del precedente.

> **Architettura di Deployment:** Il sistema core (AI + Database) risiede su un Server dedicato (es. Mac Studio o Server GPU aziendale). Gli utenti finali (tecnici, commerciali) non installano nulla in locale, ma accedono al sistema tramite una semplice e veloce Pagina Web / Web App aziendale.
> **Gestione Hardware e Watchdog:** Per evitare che il server si blocchi (Out Of Memory o Thermal Throttling) quando elabora massivamente i PDF, il sistema integra un "Watchdog". Questo componente monitora costantemente RAM e temperatura, scaricando i modelli inattivi dalla memoria prima di caricarne di nuovi (Model Multiplexing dinamico).

---

## Strategia Modelli AI (vale per tutto il piano)

Il sistema adotta una **gerarchia di modelli** che scala in base all'hardware:

| Ruolo | Modello Primario Locale | Futuro (Produzione/Server dedicati) |
|-------|------------------------|------------------------------------|
| **Cervello / Ragionatore** | Google Gemma 4 (12B o 26B MoE) con Multi-Token Prediction (tramite `mlx`) | NVIDIA Nemotron-Cascade-2 (30B MoE) o Modelli >70B su GPU dedicate |
| **Estrazione Tabelle** | DeepSeek-OCR-2 (3B) — ~3 GB a Q4 | MinerU 2.5 / olmOCR-2 |
| **Retrieval Visivo** | PixelRAG (Qwen3-VL-Embedding) | — |

**Ottimizzazione Hardware-Aware (Framework di Inferenza):**
- **Apple Silicon (Mac Locale):** Utilizzo nativo di **MLX** (`mlx-lm`) per modelli MoE (Multi-Token Prediction, es. Gemma 4). Grazie all'altissima banda passante della Memoria Unificata Apple, l'architettura MoE scala perfettamente in locale abbattendo la latenza e mantenendo un'esecuzione fluida in Q4.
- **Server/NVIDIA (Produzione/Fase 7):** Nemotron Cascade su server dedicati, utilizzando **vLLM** o **TensorRT-LLM** per massimizzare il throughput (batching e CUDA cores).

**Gestione Memoria Adattiva:**
- All'avvio il sistema rileva l'hardware (Apple Silicon vs Server) e la RAM/VRAM disponibile.
- Il context window dell'LLM primario va limitato a ~8-16K in locale per risparmiare KV cache (~2-4 GB), consentendo di tenere residenti in RAM anche gli embedder (PixelRAG) ed estrarre prestazioni massime dal context unificato senza continui offload su disco.

---

## Controllo Qualità AI (Testing di Regressione)

Durante tutto lo sviluppo, il sistema implementa un meccanismo di **Testing di Regressione (MLOps)**. 
*Cos'è in parole povere?* È come un "esame di guida" automatico per l'AI. Abbiamo creato un "Golden Dataset" di 100 domande difficili sui ricambi di cui sappiamo già la risposta perfetta. Ogni volta che modifichi il codice per aggiungere una nuova funzionalità, il sistema riprova in automatico quelle 100 domande. Se indovina, la modifica è approvata. Se sbaglia una risposta che ieri sapeva dare (una *regressione*), l'aggiornamento si ferma. Questo garantisce agli investitori e ai clienti che il sistema diventerà progressivamente solo più intelligente, senza mai "dimenticare" o peggiorare sulle cose che già sapeva fare.

---

## Fase 1 — Pipeline a Doppio Binario (Ingestion Separata)

> **Dipende da:** P1 (Gate Ingestion superato), P2 (Retrieval ibrido funzionante)
> **Obiettivo:** separare nettamente i due scopi dell'ingestion — cercare vs estrarre dati.

L'intuizione architetturale è confermata: usare un solo modello per due task opposti crea colli di bottiglia. Si adotta una Pipeline di Ingestion a **triplo binario sequenziale con Fast-Dispatch**:

### 1.0 Fast-Dispatch (Puro Testo)
- **Classificazione iniziale:** Un micro-step verifica se il PDF è "born-digital" e se la pagina contiene solo testo (es. introduzioni, indici, normative).
- **Estrazione rapida:** In caso positivo, il testo viene estratto istantaneamente tramite librerie ultra-leggere (es. `PyMuPDF`), bypassando completamente i modelli AI pesanti (PixelRAG e DeepSeek-OCR) e risparmiando enormi quantità di compute e RAM.

### 1.1 Binario Ricerca/RAG (PixelRAG)
- Tutti i PDF vengono ingeriti visivamente da PixelRAG: ogni pagina è renderizzata in tile screenshot ad alta risoluzione.
- I vettori visivi (Qwen3-VL-Embedding) vengono caricati su Qdrant.
- Il Chatbot per rispondere alle domande visive (esplosi, schemi) userà **esclusivamente** questo database per avere il contesto perfetto della pagina originale.
- **Il retrieval testuale/BM25 resta attivo in parallelo** per le query su codici alfanumerici (`AB-12345-X`) — la ricerca ibrida è indispensabile.

### 1.2 Binario Estrazione Massiva Dati (DeepSeek-OCR-2 + Generazione Strutturata)
- Gli stessi PDF (soprattutto listini e griglie) vengono processati da DeepSeek-OCR-2 (3B parametri).
- Lo scopo *non* è rispondere alle domande della chat, ma estrarre tabelle CSV/JSON strutturate per popolare PostgreSQL.
- **Enforcement dello Schema:** Per evitare output imperfetti, l'estrazione verrà supportata da framework come **PydanticAI** o **Instructor**, forzando rigorosamente il modello a rispettare gli schemi JSON/Tabelle desiderati prima dell'ingestion nel DB.
- Questo abilita il Merge accurato di Cataloghi/Listini e l'allineamento dei codici per l'ERP aziendale.
- **Gestione celle unite, tabelle multi-pagina, intestazioni ripetute**: DeepSeek-OCR-2 usa Visual Causal Flow per determinarle automaticamente — va comunque validato sul corpus reale (è il Gate P1).

### 1.3 Il Cervello Unificatore
- **Primario:** NVIDIA Nemotron-Cascade-2 (30B MoE, solo 3B attivi per token) — ~24 GB a Q4_K_M, compatibile con 32 GB di memoria unificata a 15-35 t/s su Apple Silicon.
- **Fallback:** Google Gemma 4 12B (~8-12 GB a Q4) o Gemma 4 26B MoE (~18 GB a Q4) con Multi-Token Prediction (1.4-2.2x velocità) — se Nemotron è troppo lento o la RAM è insufficiente per caricare anche gli altri modelli.
- L'LLM riceve i task dal grafo LangGraph:
  - Sbircia le immagini trovate da PixelRAG per le domande visive.
  - Interroga le righe CSV lette da DeepSeek-OCR-2 tramite **Text-to-SQL** per le domande su codici/prezzi/quantità.
  - Il routing è gestito dal nodo Router del grafo (P3).

---

## Fase 2 — Esportazione Massiva Incrociata (Catalogo + Listino)

> **Dipende da:** Fase 1.2 (DeepSeek-OCR-2 funzionante e validato)
> **Obiettivo:** unire cataloghi e listini in un database unico con codici normalizzati.

### 2.1 Funzione di Merge e Cucitura Relazionale (SQL)
- Script che unisce il **Database dei Prodotti** (estratto dai cataloghi) con il **Database dei Prezzi** (estratto dai listini) sfruttando il **Codice Normalizzato**.
- Il codice normalizzato è prodotto dalla pipeline di normalizzazione già esistente (`src/ingestion/codes.py`).
- **Linker Relazionale:** Essendo PostgreSQL la *Single Source of Truth*, la "cucitura" tra il binario strutturato (dati) e quello visivo (PixelRAG) avverrà tramite native **SQL JOIN**. Non verrà introdotto un DB a grafo (GraphRAG) per evitare overhead: il codice normalizzato funge da chiave esterna universale tra la riga di database e il file/tile screenshot associato, azzerando le latenze.

### 2.2 Gestione Errori e Scarti
- Le righe incerte vengono separate in un log **"Scarti da verificare"** con:
  - Il codice originale e il codice normalizzato tentato.
  - La pagina e il PDF sorgente (link diretto).
  - Il motivo dell'incertezza (codice non trovato nel catalogo, match ambiguo, prezzo mancante).
- Dashboard nella UI Streamlit per la revisione manuale degli scarti.

---

## Fase 3 — Fallback a Triplo Livello e Agente di Ricerca Web

> **Dipende da:** Fase 1.3 (Cervello Unificatore operativo), P3 (Grafo LangGraph funzionante)
> **Obiettivo:** garantire una risposta anche quando il RAG interno non basta.

Il sistema implementa una **cascata di fallback** orchestrata dal grafo LangGraph:

### Livello 1 — RAG Interno (alta confidenza)
- Cerca nei PDF via PixelRAG + BM25 ibrido.
- Se la confidenza retrieval è > 70% → risposta sicura con allegato (pagina PDF, immagine esploso).
- Il guardrail deterministico verifica che ogni codice citato esista letteralmente nella tabella recuperata.

### Livello 2 — Conoscenza LLM Base (solo intenti discorsivi)
- Se la domanda è di natura puramente conversazionale o di meta-spiegazione (es. "come funziona il sistema", "ciao"), risponde l'intelligenza nativa del modello.
- Se la domanda è **tecnica o commerciale** e il RAG fallisce, l'LLM ha il divieto assoluto di usare la propria conoscenza appresa per evitare allucinazioni pericolose su ricambi industriali.
- In tal caso il sistema passa automaticamente al Livello 3 (Web Agent) oppure emette un "Non ho documenti a riguardo".
- Le risposte L2 di natura generica sono marcate come **"Conoscenza generale — non verificata sui tuoi documenti"**.

### Livello 3 — Agente Web Autonomo (ultima risorsa)
- Se anche il Livello 2 non è sufficiente, un agente autonomo cerca su Internet.
- **Strumenti:** Tavily per la ricerca web ottimizzata AI + Browser-Use/Playwright MCP per navigazione.
- **Guardie di sicurezza obbligatorie e Observability:**
  - **Isolamento e Igienizzazione (Sanitization):** Per evitare attacchi di *Prompt Injection* (es. siti web malevoli che tentano di manipolare l'AI per falsificare codici), i dati letti da internet passano in una "sandbox". Un modello LLM secondario "pulitore" estrae solo i fatti crudi dal sito web, rimuovendo qualsiasi istruzione potenzialmente pericolosa prima di passarla al Cervello Unificatore aziendale.
  - **Watchdog / Sandbox:** Implementazione di un sistema di tracing (es. Langfuse o logging strutturato) per prevenire "infinite loop" di navigazione. Se l'agente rivisita domini/URL senza successo, il watchdog forza un'uscita anticipata.
  - Budget token massimo e timeout (es. 60 secondi, 10K token).
  - Red lines: nessun acquisto, nessun login, nessuna azione che modifichi dati.
  - La risposta deve citare gli URL sorgente verificabili.
  - Il risultato è marcato come **"Fonte esterna — verificare prima dell'uso"**.
- L'agente compila un report strutturato con fonti, che viene presentato all'utente.

---

## Fase 4 — Interfaccia Utente Chainlit (PoC → Demo Vendibile)

> **Dipende da:** Fase 1 (pipeline funzionante), P4 (RBAC attivo)
> **Obiettivo:** una demo con "wow effect" utilizzabile da utenti non tecnici.

### 4.1 Chat + Proof View
- Adozione di **Chainlit** anziché Streamlit. Chainlit supporta nativamente LangGraph, gestisce sessioni multiple in concorrenza senza blocchi asincroni e offre un feeling molto simile a chat enterprise.
- La risposta mostra:
  1. **Testo ancorato** + livello di sicurezza (Alta/Media/Bassa/Non lo so).
  2. **Pagina esatta citata** con numero e titolo.
  3. **Rendering visivo** della pagina PDF / esploso affiancato alla risposta.
  4. **Stock** (simulato in Fase 1, reale dopo integrazione ERP).
  5. **Avviso versione obsoleta** se il documento è marcato "sostituito".
  6. **Indicazione del Livello** usato (RAG Interno / Conoscenza LLM / Fonte Esterna).

### 4.2 Pannello Admin
- Schermata per aggiungere/modificare regole RegEx ai brand con interfaccia guidata.
- Dashboard scarti del Merge (Fase 2.2).
- Visualizzazione metriche: recall@k, accuratezza, distribuzione confidenza.

### 4.3 Ciclo di Feedback (Active Learning)
- Ogni risposta offre pulsanti di feedback nativi forniti da Chainlit: **"Corretto"**, **"Sbagliato"**.
- Il feedback viene salvato in modo **strutturato** (query + contesto recuperato + fonti citate + giudizio).
- Le correzioni alimentano il set di valutazione, **con moderazione** (admin quarantine) prima di entrare nel ground truth.

> **Nota architetturale:** Chainlit risolve il problema di stabilità multi-utente e l'overhead di Streamlit in streaming, coprendo perfettamente la transizione finché non sarà necessario il refactoring totale con FastAPI + React/Vue (vedi Fase 7).

---

## Fase 5 — Memoria a Lungo Termine Multi-Utente

> **Dipende da:** Fase 4 (UI funzionante con auth)
> **Obiettivo:** il sistema ricorda le preferenze e il contesto di ogni utente nel tempo.

### 5.1 Piramide Semantica a 4 Livelli (TencentDB Agent Memory)
- **L0 — Conversazione:** Salvataggio raw di ogni sessione di chat.
- **L1 — Fatti Atomici:** Estrazione automatica dei fatti chiave dalla conversazione (es. "L'utente cerca sempre ricambi per compressori Atlas Copco").
- **L2 — Scenari:** Raggruppamento dei fatti in pattern d'uso (es. "Manutenzione programmata trimestrale").
- **L3 — Profilo Utente/Aziendale (Persona):** Profilo ad alto livello con preferenze consolidate, consultato per primo per personalizzare le risposte.

### 5.2 Implementazione e Iniezione Dinamica
- **Backend:** SQLite + sqlite-vec per la Fase locale (zero dipendenze cloud); migrazione a PostgreSQL per produzione.
- La libreria TencentDB Agent Memory è open-source (Maggio 2026) e supporta nativamente la piramide L0-L3.
- **Multi-tenant:** lo schema `core.tenants` + `core.agents` su PostgreSQL isola i dati tra aziende diverse.
- **Iniezione nel System Prompt:** Invece di far interrogare la memoria all'LLM tramite un tool (con latenza annessa), i Fatti Atomici (L1) e il Profilo (L3) più pertinenti vengono iniettati in tempo reale nel *System Prompt* del Cervello Unificatore (max 500 token). In questo modo il modello "sa" intrinsecamente chi è l'utente fin dalla prima interazione.

---

## Fase 6 — Integrazione Gestionale ERP (Sola Lettura)

> **Dipende da:** Fase 2 (Merge funzionante), Fase 4 (UI), accordo commerciale con il cliente
> **Obiettivo:** dati reali di stock e prezzi nel sistema, senza rischio di scrittura sull'ERP.

### 6.1 Sincronizzazione Notturna (Cron Job)
- Job schedulato che esegue un **upsert** dello stock aggiornato in tabelle PostgreSQL.
- Sorgente: export dal gestionale del cliente (CSV/API a seconda dell'ERP — TeamSystem, Zucchetti, SAP Business One, Danea).
- I dati sincronizzati: codici articolo, giacenze, prezzi di listino, stato (attivo/obsoleto).

### 6.2 Interrogazione dal Grafo (Semantic Layer)
- Il nodo Text-to-SQL **non interrogherà mai** le tabelle ERP originali, caratterizzate da schemi spesso caotici (es. nomi colonna ambigui o relazionalità implicita).
- Il sistema interrogherà un **Semantic Layer**: un set di "Viste Materializzate" specifiche per l'AI in PostgreSQL, che traducono ed espongono i dati in uno schema pulito e comprensibile.
- L'utente chiede "Quanti ne abbiamo a magazzino?" → l'LLM fa la query sulle viste pulite e risponde con dati reali.
- La risposta indica sempre la data dell'ultimo aggiornamento sync.

---

## Fase 7 — Refactoring verso SaaS (Futuro)

> **Dipende da:** Fasi 1-6 completate e validate su almeno 2-3 clienti reali
> **Obiettivo:** passaggio da PoC locale single-tenant a piattaforma multi-tenant.

Questa fase si attiva **solo dopo** aver dimostrato il valore del sistema e avere metriche di onboarding reali.

### 7.1 Refactoring Backend
- Da Streamlit a **FastAPI** dedicato + frontend separato (**React** o **Vue.js**).
- Autenticazione JWT multi-ruolo/multi-tenant.
- API REST/WebSocket per la chat.
- API di amministrazione profili brand.
- API versioning (`/api/v1/`), CORS, rate limiting.

### 7.2 Refactoring Frontend
- UI professionale con React (ecosistema più ampio) o Vue.js (team piccolo).
- Chat + Proof View, Pannello Admin, Dashboard metriche — tutto ricostruito con framework moderno.
- Responsive design, branding personalizzabile per cliente.

### 7.3 Infrastruttura di Produzione
- Multi-tenancy isolata: collezione-per-tenant + filtro dati + test automatici anti-leak.
- Modelli AI su **server dedicati con GPU** (RTX 4090/A100) → possibilità di usare modelli >70B.
- Inferenza condivisa: coda richieste + batching per abbattere il costo/query.
- Logging e osservabilità (LangSmith o equivalente).
- CI/CD, test automatici, graceful degradation.

---

## Riepilogo Dipendenze

```
P0-P4 (SPEC.md)
  │
  ├──→ Fase 1: Pipeline Doppio Binario (PixelRAG + DeepSeek-OCR-2 + Cervello)
  │       │
  │       ├──→ Fase 2: Merge Cataloghi/Listini
  │       │
  │       ├──→ Fase 3: Fallback Triplo Livello + Agente Web
  │       │
  │       └──→ Fase 4: UI Streamlit + Feedback
  │               │
  │               ├──→ Fase 5: Memoria a Lungo Termine
  │               │
  │               └──→ Fase 6: Integrazione ERP
  │
  └──→ [Dopo validazione su 2-3 clienti]
          │
          └──→ Fase 7: Refactoring SaaS (FastAPI + React/Vue.js)
```
