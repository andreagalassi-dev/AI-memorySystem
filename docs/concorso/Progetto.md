# AI Knowledge Engine — Documento di Progetto Architetturale (Deep Dive)

Questo documento fornisce un'analisi profonda ed esaustiva dell'architettura, delle funzionalità, dei modelli e delle scelte ingegneristiche alla base dell'**AI Knowledge Engine**. 

Il sistema è stato concepito per risolvere un problema critico nel settore industriale (B2B): la frammentazione della conoscenza tecnica e commerciale tra migliaia di PDF (cataloghi, esplosi, manuali) e gestionali aziendali. L'obiettivo non è creare un semplice "chatbot", ma un **Agente Operativo Tecnico-Commerciale**, capace di estrarre Distinte Base (BOM), generare bozze di preventivi e rispondere a quesiti complessi in tempo reale.

Tutto il progetto è guidato dal paradigma della **Defensive AI Engineering**: in ambito industriale, un'allucinazione (es. inventare il codice di un ricambio o sbagliarne il prezzo) genera danni economici reali. Per questo, l'Intelligenza Artificiale è incapsulata all'interno di flussi software puramente deterministici.

---

## 1. Architettura di Ingestion: Come il sistema impara

Il sistema abbandona il classico approccio RAG (Retrieval-Augmented Generation) "tutto testo", inefficace su documenti industriali, in favore di una **Pipeline a Triplo Binario**.

### 1.1 Binario Fast-Dispatch (Puro Testo)
- **Cos'è:** Un classificatore in ingresso (basato su `PyMuPDF/fitz`) analizza i PDF pagina per pagina. Se rileva pagine "born-digital" puramente testuali (es. indici, avvertenze, manualistica), estrae il testo istantaneamente.
- **Perché:** Evita di sprecare preziosa potenza di calcolo (Compute/VRAM) passando il testo semplice a modelli AI pesanti, ottimizzando radicalmente i tempi di ingestion.

### 1.2 Binario Strutturato: GLM-OCR e PydanticAI
- **Cos'è:** Le pagine contenenti griglie e listini prezzi vengono passate a **GLM-OCR**.
- **Il Modello:** GLM-OCR è un modello Vision (VLM) specializzato nel *Visual Causal Flow*. Riesce a "capire" la struttura logica di tabelle complesse, celle unite e intestazioni multi-pagina, dove i classici OCR estraggono solo stringhe di testo senza senso.
- **Enforcement (La Gabbia Pydantic):** Il modello non è lasciato libero. Tramite framework come *PydanticAI* (o Instructor), GLM-OCR è costretto a restituire i dati rispettando uno schema JSON rigidissimo (`extra="forbid"`). Il modello viene usato come un mero "estrattore-passacarte": se prova ad allucinare una colonna o a cambiare formato, il codice rifiuta l'output.
- **Normalizzazione:** Il `codice_normalizzato` (es. da "0US-123.A" a "0us123a") viene calcolato da script Python deterministici, mai dall'LLM.

### 1.3 Binario Visivo: PixelRAG (Qwen3-VL-Embedding)
- **Cos'è:** Le pagine con schemi tecnici ed esplosi grafici vengono rasterizzate e passate a un modello di embedding visivo (`Qwen3-VL-Embedding`) che le trasforma in vettori archiviati in `Qdrant`.
- **Perché:** Quando l'utente chiede "Fammi vedere l'esploso del componente", il sistema non cerca parole, ma usa la similarità semantica visiva per restituire esattamente il ritaglio del PDF originale.

### 1.4 Merge Relazionale (La Single Source of Truth)
Una volta estratti, i dati del catalogo e i dati del listino prezzi vengono incrociati. Per evitare i ritardi e le incertezze dei database a grafo (GraphRAG), il sistema usa **PostgreSQL**. Il *codice normalizzato* funge da chiave primaria (Primary Key) infallibile per le `SQL JOIN`, collegando istantaneamente il prezzo di un ricambio alla sua immagine in Qdrant. Gli scarti vengono messi in una Dashboard per la risoluzione umana.

---

## 2. Il "Cervello Unificatore": Orchestrazione ed Esecuzione

Quando l'utente pone una domanda, il sistema non la invia "alla cieca" a un LLM. L'esecuzione è governata da una rigida Macchina a Stati Finiti (FSM) costruita con **LangGraph** (`src/graph/pipeline.py`).

### 2.1 I Modelli di Ragionamento
- Per il cervello principale, l'architettura punta a modelli **MoE (Mixture of Experts)** come `Gemma 4` (12B o 26B) o `Nemotron-Cascade-2`.
- **Hardware-Aware (Apple Silicon):** Il sistema è ottimizzato per girare in locale su hardware Apple sfruttando il framework `MLX`. La Memoria Unificata dei chip Apple (M2/M3/M4 Ultra) offre una banda passante immensa, permettendo ai modelli MoE di raggiungere velocità di inferenza (token/s) eccellenti a precisione Q4, abbattendo la latenza e mantenendo il sistema privato (on-premise).

### 2.2 Il Router e gli Agenti Specializzati
La domanda dell'utente passa dal Nodo Router (LangGraph) che ne deduce l'intento:
1. **Intento Structured (Text-to-SQL):** Se l'utente chiede *"Quanto costa il pezzo X?"*, la query viene passata a un esecutore SQL. L'LLM *non formula la risposta da zero*, ma formatta i dati recuperati chirurgicamente dal database.
2. **Intento Visual:** Se chiede un'immagine, si attiva il PixelRAG.
3. **Intento Narrative:** Usa il RAG ibrido (Dense + BM25) classico per i manuali.

### 2.3 Fallback a Cascata a 3 Livelli (Fase 3)
Se l'informazione non è trovata immediatamente, il Grafo LangGraph attiva dei fallback:
1. **Livello 1 (RAG Interno):** Massima priorità sui PDF aziendali.
2. **Livello 2 (Conoscenza LLM):** Usata *esclusivamente* per risposte discorsive ("Come funziona il sistema?"). **Bloccata severamente** per argomenti tecnici per evitare allucinazioni letali.
3. **Livello 3 (Web Agent Igienizzato):** Un agente (es. Tavily) cerca sui siti dei produttori. I dati web vengono prima passati in una **Sandbox di Igienizzazione**: un LLM secondario pulisce il testo dai comandi dannosi (difesa da *Prompt Injection*) estraendo solo "fatti crudi" da passare al cervello principale.

---

## 3. Solidità del Sistema: L'Ingegneria Zero-Allucinazioni

Ciò che distingue questo progetto da un prototipo è l'infrastruttura difensiva (`src/retrieval/guardrail.py`):

- **Guardrail Deterministico sui Codici:** Prima che una risposta venga mostrata all'utente, un modulo Python analizza l'output dell'LLM usando Regex stringenti per trovare ogni singolo codice citato. Questo codice viene confrontato **letteralmente** con i documenti estratti dal DB. Se l'LLM ha citato un codice che non esiste nell'evidenza testuale, la risposta viene `BLOCCATA` (se non a catalogo) o retrocessa a `VERIFICA MANUALE` (se ambiguo). 
- **Fail-Closed Design:** Se un servizio cade (es. timeout di Postgres o Qdrant), il sistema non tenta di indovinare, né va in crash. LangGraph gestisce l'eccezione tipizzata sollevando un *System Error* e restituendo educatamente *"Sistema non disponibile"*.
- **Controllo Ciclo di Vita (Watchdog):** Un worker monitora le cartelle. Se un PDF viene spostato nel cestino, il DB lo marca come `deleted`. Il sistema intercetta a livello di grafo questo stato e **inietta obbligatoriamente un alert rosso** ("Documento Eliminato/Obsoleto") nella risposta dell'AI.
- **MLOps e Regression Testing:** Ogni modifica al codice deve passare una suite di test automatici basati su un "Golden Dataset" di 100 domande/edge-cases complessi. Se un aggiornamento fa regredire il sistema (un codice esatto ieri diventa sbagliato oggi), la modifica non va in produzione.

---

## 4. Evoluzione e Applicazioni Operative

Il sistema è progettato per scalare dal PoC iniziale a una piattaforma SaaS Multi-Tenant.

### 4.1 Automazione del Lavoro (BOM e Preventivi)
Oltre alla ricerca, l'AI è in grado di operare estrazioni logiche (Hop 2). Dato l'identificativo di un assieme (kit), recupera i `parent_id` collegati estraendo la **Distinta Base (BOM)** completa. Accettando liste sporche di richieste testuali dai clienti, l'AI esegue il matching e restituisce **bozze di preventivo** in tabelle pulite pronte per la validazione umana.

### 4.2 Integrazione ERP Sicura (Fase 6)
Il Knowledge Engine verrà collegato ai gestionali aziendali (SAP, Zucchetti, TeamSystem) per fornire Stock e Prezzi in tempo reale. Per evitare vulnerabilità di sicurezza:
- L'AI non avrà **mai** accesso in scrittura al DB del gestionale.
- Le query Text-to-SQL colpiranno un **Semantic Layer** (Viste Materializzate in PostgreSQL aggiornate da Export Batch Notturni), dove le colonne astruse dell'ERP sono state preventivamente tradotte in "linguaggio potabile" per l'Intelligenza Artificiale.

### 4.3 UI e Piramide della Memoria (Fasi 4 e 5)
L'interfaccia si evolverà da `Chainlit` (stabile per multi-threading e websocket) a `React + FastAPI`. 
L'esperienza sarà potenziata dall'architettura **TencentDB Agent Memory**, strutturata in 4 livelli (L0-L3). Invece di far leggere all'LLM migliaia di token di storico chat, un job di background distilla un "Profilo Utente L3" (es. 200 token). Questo profilo iper-compresso viene **iniettato nativamente nel System Prompt** all'inizio della sessione, garantendo una personalizzazione estrema e istantanea a costo di latenza (e token) quasi zero. Ad ogni utente saranno applicate policy RBAC (Role-Based Access Control) rigorose, garantendo l'isolamento dei dati.

---
*Conclusione:* L'AI Knowledge Engine unisce i più avanzati modelli multimodali (VLM, MoE) a un layer di ingegneria del software rigorosa, offrendo al settore manifatturiero e commerciale uno strumento autonomo potente, ma soprattutto, intrinsecamente **affidabile e sicuro**.
