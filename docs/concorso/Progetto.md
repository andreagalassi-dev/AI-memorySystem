# AI Knowledge Engine — Visione Prodotto (North Star)

> **Cos'è questo documento:** la descrizione del prodotto definitivo verso cui si lavora.
> Ogni funzionalità è marcata con lo **stato reale** e con l'**epica** del piano operativo
> (`docs/piano_implementazione/15_piano_prodotto.md`) che la consegna. Si aggiorna a fine
> di ogni epica. Ultimo allineamento: **2026-07-15** (giorno della demo riuscita).
>
> Legenda: ✅ esiste e funziona · ⚠️ esiste ma parziale/da irrobustire · ❌ da costruire

Il sistema risolve un problema critico del settore industriale (B2B): la frammentazione
della conoscenza tecnica e commerciale tra migliaia di PDF (cataloghi, esplosi, manuali,
brochure) e gestionali aziendali. L'obiettivo non è un "chatbot", ma un **Agente
Operativo Tecnico-Commerciale**: estrae Distinte Base, genera bozze di preventivi,
risponde a quesiti complessi in linguaggio naturale o per codice. Target di prodotto:
**≥90% di risposte corrette e utili**; il "non lo so" è raro e sempre motivato — quando
il corpus non basta, il sistema cerca online con fonti citate (cascata a 3 livelli, §2).

Paradigma guida: **Defensive AI Engineering** — in ambito industriale un'allucinazione
(un codice inventato, un prezzo sbagliato) genera danni economici reali. L'AI è
incapsulata in flussi software deterministici. Target di qualità: precision ALTA ≥ 0.98
al gate di regressione, con l'errore silenzioso trattato sempre come bug da eradicare.

---

## 1. Ingestion: come il sistema impara

**Pipeline a Triplo Binario:**

- **1.1 Fast-Dispatch (puro testo)** ✅ — classificatore PyMuPDF pagina-per-pagina,
  estrazione istantanea del born-digital testuale, zero VRAM sprecata.
- **1.2 Binario Strutturato (tabelle e listini)** ⚠️ → **Epiche B1+C** — routing
  born-digital→`TextTableExtractor` (codici esatti) / scansioni→GLM-OCR con gabbia
  Pydantic (`extra="forbid"`) e grounding verbatim. **Stato reale:** l'estrazione
  massiva ha prodotto dati non verbatim (descrizioni collassate, coperture mancanti:
  Mamoli fermo a p185/285) e solo 59 PDF su 141 sono indicizzati. Il ridisegno su
  audit è l'epica C; i prezzi entrano nel prodotto solo oltre il gate.
- **1.3 Binario Visivo — PixelRAG** ⚠️ → **Epica E** — pagine grafiche rasterizzate,
  embedding Qwen3-VL single-vector 1024d + reranker, tile→pagina, RBAC fail-closed.
  **Stato reale:** funziona (demo), ma recall 40% sul set eval col profilo corrente e
  1,5-2 min/query a freddo su MPS. Serve: modelli residenti, batch, eval ampliato,
  cross-lingua IT.
- **1.4 Merge Relazionale (Single Source of Truth)** ⚠️ → **Epica C** — PostgreSQL,
  `codice_normalizzato` come chiave, niente GraphDB. **Stato reale:** implementato e
  provato, ma a corpus reale produce 11 linked / 2320 orfani (le pagine di listino non
  stanno nell'indice visivo): design della riconciliazione da ripensare. La **dashboard
  scarti per la risoluzione umana** è da costruire (❌, epica C).

## 2. Il Cervello Unificatore

- **FSM LangGraph tri-intent** (structured / visual / narrative) ✅ — costruita e provata
  end-to-end su adapter reali. ⚠️ → **Epica A**: oggi vive su un branch non integrato;
  la UI di prodotto non ci è ancora collegata.
- **Modelli di ragionamento** ⚠️ → **Epiche A/G** — MLX Gemma 4 26B MoE sul Mac
  (implementato, lento per l'uso live), Ollama come fallback. Profilo doppio: 
  mac-ottimizzato ora, NVIDIA (DGX Spark GB10, 128GB unificati, CUDA) quando arriva.
- **Risposta a cascata 3 livelli — "non lo so" deve diventare RARO** ❌ → **Epica H**
  (decisione utente 15/07: feature portante). Il principio fondante evolve: da "mai
  fuori dal corpus" a **"mai un'affermazione senza fonte"** — ogni risposta dichiara
  la provenienza. La cascata:
  1. **Corpus interno** (priorità massima): documenti aziendali, con citazione doc+pagina.
  2. **Agente di ricerca web**: se il corpus non basta, un agente RAGIONA sulla domanda,
     cerca online, **riporta sempre le fonti (URL)** e passa dalla **sandbox di
     igienizzazione** (difesa da prompt injection: al cervello arrivano solo fatti crudi).
  3. **Conoscenza dell'LLM** (es. "qual è la capitale della Francia?" → DEVE rispondere):
     utilizzabile, ma **sempre verificata online tramite l'agente di ricerca**, con fonti.
  Regola speciale codici/prezzi (dove l'errore costa denaro): dal corpus la risposta è
  garantita dal guardrail (ALTA); se trovata SOLO online, viene mostrata con **warning
  ben visibile e inequivocabile** ("informazione trovata online, correttezza NON
  verificata" + fonte) e non è mai confusa con una risposta a catalogo. È il **feedback
  degli utenti a promuoverla**: quando viene confermata come corretta (con soglia di
  conferme o moderazione — da affinare), entra nella memoria aziendale e diventa la
  prima risposta per chiunque la richieda dopo. Vincolo di deployment: l'uscita web è
  configurabile per cliente (installazioni air-gapped: cascata ferma al livello 1).
- **RAG documentale generico** ⚠️ → **Epiche A/C** — non solo cataloghi ricambi:
  qualunque documento aziendale (es. una brochure: "cosa fa questa azienda?") deve
  produrre una risposta elaborata dal contenuto, senza inventare; se il documento non
  basta, la cascata prosegue al livello 2. Il ramo NARRATIVE esiste; va potenziato
  su documenti non-catalogo e coperto dal golden gate.

## 3. Zero-Allucinazioni (l'infrastruttura difensiva)

- **Guardrail deterministico sui codici** ✅ — regex + confronto letterale con
  l'evidenza; blocco o retrocessione a "verifica manuale". In demo ha bloccato
  correttamente riferimenti riformulati dal generatore: fa il suo lavoro.
- **Risposta STRUCTURED deterministica (l'LLM formatta, non genera)** ⚠️ → **Epica D**
  — il principio è dichiarato ma il generatore oggi riformula (es. "IEC 34-7" vs
  "EN60034-7" nell'evidenza): la risposta sui dati strutturati deve diventare
  template-driven, con l'LLM solo come formattatore.
- **Fail-Closed design** ⚠️ → **Epica A (T3)** — la degradazione oggi è silenziosa
  (`except: dep=None`): servono warning strutturati e `/health` con stato dei rami.
- **Watchdog ciclo di vita documenti** ✅ — ingestion per-documento, avviso obsoleto
  in risposta, cancellazione → alert.
- **MLOps / Golden Dataset gate** ❌ → **Epica B2** — il gate anti-regressione con
  baseline e metriche per lingua (IT/EN/DE) non esiste ancora: è il metro di tutto.

## 4. Evoluzione e applicazioni operative

- **BOM (Distinta Base)** ⚠️ — estrazione parent/child implementata (fase D1);
  da integrare e provare nel prodotto unificato (epica A e oltre).
- **Bozze di preventivo da liste sporche** ❌ — bozza su branch `feat/fase-d2-quote`,
  mai integrata; decisione in epica A.
- **Integrazione ERP sicura (semantic layer, sola lettura)** ❌ — post-epiche ("Fase 6").
- **UI**: ⚠️ → **Epica A** — salto a **Chainlit** (auth, sessioni, feedback nativi);
  React+FastAPI resta il target di lungo periodo. La demo Streamlit congelata resta
  come vetrina permanente. **Stato 19/07:** scheletro Chainlit VIVO (auth per ruolo,
  answer() reale, collaudo utente: codice→ALTA con fonte). **Requisito utente 19/07:**
  la pagina citata deve comparire renderizzata in chat e il viewer deve essere
  **navigabile** (zoom sull'esploso, scorrimento tra le pagine del documento) — render
  in chat in A7.2, navigazione piena tra A7.2 ed epica E.
- **Memoria a tre livelli e apprendimento dai feedback** ❌ → **Epica F** — il sistema
  migliora con l'uso. Struttura gerarchica:
  1. **Memoria aziendale (generale)**: conoscenza condivisa da tutti — es. una risposta
     trovata online e confermata dagli utenti diventa la prima risposta per chiunque;
  2. **Memoria di categoria** (amministrazione, tecnici, commerciali…): agganciata al
     RBAC — decide "cosa si può dire a chi" e memorizza ciò che serve a quel ruolo;
  3. **Memoria personale ("Mario Rossi")**: se un utente chiede ogni settimana la stessa
     cosa e ha già indicato via feedback la risposta giusta, il sistema la ripropone.
  Promozione verso l'alto (personale → categoria → aziendale) SOLO con conferme multiple
  o moderazione (mai da un singolo feedback: un errore confermato da uno non deve
  inquinare tutti). Profilo utente compresso stile L0-L3 iniettato nel system prompt.
  Il feedback si raccoglie da subito (schema progettato in epica A).
- **SaaS multi-tenant** ❌ — fuori orizzonte corrente (era "Fase 7").

---

*Il divario tra questa visione e lo stato reale è il piano operativo: 
`docs/piano_implementazione/15_piano_prodotto.md` (epiche A→G).*
