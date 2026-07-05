# Fase 3 — Fallback a Triplo Livello e Agente di Ricerca Web

> **Dipende da:** Fase 1.3 (Cervello Unificatore operativo), P3 (Grafo LangGraph funzionante)
> **Obiettivo:** Garantire una risposta sicura all'utente anche quando il database interno (RAG) non contiene le informazioni necessarie, evitando sempre le allucinazioni.

Il sistema implementa una **cascata di fallback** orchestrata in modo deterministico dal grafo LangGraph:

## Livello 1 — RAG Interno (Alta Confidenza)
- Il sistema cerca nei PDF aziendali via PixelRAG + BM25 ibrido (Qdrant).
- Se la confidenza del retrieval è > 70% → viene erogata una risposta sicura con allegato obbligatorio (pagina PDF, immagine dell'esploso tecnico).
- **Guardrail Deterministico:** Un blocco di codice verifica che *ogni singolo codice* citato dall'LLM nella risposta esista letteralmente nella tabella recuperata dal DB. Se l'LLM inventa un codice o inverte dei numeri, la risposta viene bloccata.

## Livello 2 — Conoscenza LLM Base (Solo intenti discorsivi)
- Se la domanda è di natura puramente conversazionale o esplicativa del sistema (es. "come fai a trovare i prezzi?", "ciao"), risponde l'intelligenza nativa del modello.
- **Blocco Allucinazioni:** Se la domanda è **tecnica o commerciale** (es. "Mi serve la guarnizione X") e il RAG di Livello 1 fallisce, l'LLM ha il divieto assoluto di usare la propria conoscenza pregressa (appresa in addestramento). In ambito manifatturiero, sbagliare un ricambio può causare danni enormi.
- Le risposte di Livello 2 generiche sono marcatamente evidenziate come **"Conoscenza generale — non verificata sui tuoi documenti"**.

## Livello 3 — Agente Web Autonomo (Ultima Risorsa)
- Se la risposta non è nei documenti e non è discorsiva, un agente autonomo viene delegato a cercare su Internet.
- **Strumenti:** Tavily per la ricerca web ottimizzata per AI, combinato con Browser-Use/Playwright per l'estrazione dati dai siti web dei produttori.
- **Guardie di sicurezza obbligatorie e Observability:**
  - **Isolamento e Igienizzazione (Sanitization):** Per evitare attacchi di *Prompt Injection* (es. un sito web malevolo che contiene istruzioni nascoste per falsificare la risposta del bot), i dati letti da internet passano prima in una "sandbox". Un modello LLM secondario (più piccolo e rigido) ha il compito esclusivo di estrarre "solo i fatti crudi" dal testo web, ripulendolo da ogni comando potenziale, prima di passarlo al Cervello principale.
  - **Watchdog Navigazione:** Sistema di tracing (Langfuse) per prevenire "infinite loop". Se l'agente inizia a girare a vuoto sugli stessi link, viene interrotto.
  - Limite budget token e timeout fisso (es. massimo 60 secondi d'attesa, 10K token usati).
  - **Red Lines:** Divieto assoluto di effettuare login automatizzati o compilare form d'acquisto (modalità Read-Only garantita).
  - L'output deve mostrare gli URL sorgente e il badge: **"Fonte esterna — verificare prima dell'uso"**.
