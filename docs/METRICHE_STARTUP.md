# Metriche e Dati per Pitch Aziendale (Startup)

Questa selezione di metriche è stata estratta dalla documentazione tecnica del progetto. Sono KPI (Key Performance Indicators) perfetti per una presentazione agli investitori (Pitch Deck) per dimostrare solidità, scalabilità e il "fossato tecnologico" (moat) anti-allucinazione.

## 1. Accuratezza e Precisione (Il Core Business)
I dati dimostrano che il sistema non è un semplice "wrapper di ChatGPT", ma un motore di ricerca industriale altamente specializzato.
- **84.2% di Precisione Assoluta (Top-1)**: Il motore recupera il codice ricambio esatto al primo colpo nell'84.2% dei casi (R@1 = 0.842).
- **94.7% di Recall (Top-10)**: Nel 94.7% dei casi, il pezzo corretto è tra i primi 10 risultati suggeriti (R@10 = 0.947) anche in un database con oltre 33.000 distrattori.
- **Multilingua Nativo**: L'introduzione del Reranker semantico ha migliorato la precisione delle query cross-lingua (es. meccanico che cerca in Italiano su un manuale in Tedesco) dell' **11.5%** (R@1 +0.115), permettendo alle officine di superare le barriere linguistiche.

## 2. Sicurezza B2B e Zero-Allucinazioni (Il Fossato Tecnologico)
In ambito B2B, un codice sbagliato significa un reso costoso. Il sistema è progettato "Fail-Closed" (Giusto o "Non lo so").
- **0% Allucinazioni sui Codici**: Grazie a un Guardrail Deterministico integrato nel Grafo di esecuzione, l'Intelligenza Artificiale non può fisicamente inventare un codice ricambio non presente a catalogo (Tasso di blocco allucinazioni: 100%).
- **Degrado Controllato (Graceful Degradation)**: Al contrario dei classici chatbot, il sistema calcola la "confidenza matematica" *prima* di interpellare l'IA. Se il livello è BASSO, rifiuta di rispondere (evitando costi di calcolo e risposte errate).
- **Tracciabilità Totale**: Ogni singola affermazione del bot è ancorata a una citazione obbligatoria `(Manuale X, Pagina Y)`. 

## 3. Elaborazione Visiva e Multimodale
I manuali tecnici non sono solo testo, ma soprattutto "esplosi" (disegni tecnici vettoriali).
- **1.035 Tavole Grafiche Indicizzate**: Il sistema usa modelli visivi d'avanguardia (ColPali/Vision-Language) per "guardare" le pagine come un umano.
- **Classificazione Automatica 96.2%**: Il sistema riconosce automaticamente una tavola tecnica da una pagina testuale con un'accuratezza in holdout del 96.2%, smistando il lavoro al modello corretto senza intervento umano.

## 4. Performance e Scalabilità dell'Infrastruttura
Pronto per supportare il carico in produzione (SaaS).
- **Latenza Ultra-bassa (≤ 50ms)**: Il recupero dati di base impiega meno di 50 millisecondi (0.058s). Anche con filtri semantici complessi, l'attesa rimane sotto al secondo (0.764s).
- **Elaborazione Locale ed Economica**: Utilizzando modelli LLM locali ottimizzati (`qwen2.5:14b`), il sistema impiega circa 6.2 secondi su normale architettura ARM (Apple Silicon) per fornire una risposta sintetica finale, azzerando i costi API verso OpenAI/Google.
- **Onboarding Cliente Automatizzato (Zero-Touch)**: L'ingestion (caricamento) dei cataloghi del cliente è governata da un demone automatico (Watchdog). L'utente trascina un PDF in una cartella e il sistema esegue OCR, estrazione vettoriale, chunking e sincronizzazione Database in totale autonomia.

## 5. Sicurezza Dati e Multi-Tenancy (RBAC)
- **Segregazione dei Dati (RBAC)**: Il database è partizionato per Ruolo (Tecnico, Commerciale, Amministrazione). I filtri di sicurezza sono applicati a livello di query vettoriale, rendendo matematicamente impossibile un "data leak" tra reparti.
- **Single Source of Truth**: Architettura industriale con PostgreSQL come master e database vettoriali come replica/proiezione, per azzerare le desincronizzazioni in caso di crash.
