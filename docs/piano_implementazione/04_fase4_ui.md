# Fase 4 — Interfaccia Utente Chainlit (PoC → Demo Vendibile)

> **Dipende da:** Fase 1 (pipeline funzionante), P4 (RBAC attivo)
> **Obiettivo:** Trasformare l'output crudo dell'AI in una demo con "Wow Effect", offrendo un'esperienza d'uso fluida e stabile per gli utenti aziendali.

## 4.1 Chat + Proof View (Perché Chainlit)
- Prevediamo l'adozione di **Chainlit** anziché Streamlit per l'interfaccia di produzione iniziale.
- *Motivazione:* Streamlit è ottimo per piccoli script, ma sotto carico soffre il ricaricamento asincrono continuo della pagina. Chainlit supporta nativamente i flussi LangGraph, gestisce le websocket in modo stabile per le sessioni multi-utente e offre un layout chat molto più vicino agli standard enterprise (simil-ChatGPT).
- **Elementi della Risposta UI:**
  1. **Testo ancorato** con badge di sicurezza visibile (Alta/Media/Bassa/Esterna).
  2. **Pagina esatta citata** con numero e titolo del documento sorgente.
  3. **Rendering visivo:** L'immagine della pagina PDF o l'esploso del pezzo viene renderizzata in un pannello laterale (Proof View) contestualmente alla risposta.
  4. **Stock e Prezzo** recuperati in tempo reale dal database.
  5. **Avviso Documento Obsoleto:** Se la fonte usata dall'AI è marcata come `superseded` in PostgreSQL, appare un grosso alert.

## 4.2 Pannello Amministrativo (Admin View)
- Schermata di configurazione integrata per gestire i brand e aggiungere/modificare le regole RegEx (es. per nuovi formati di codici pezzo) tramite interfaccia guidata, senza toccare il codice python.
- Dashboard scarti del Merge (vedi Fase 2).
- Visualizzazione metriche di base: Recall@k, tempo di latenza medio, numero di fallback al Web Agent.

## 4.3 Ciclo di Feedback (Active Learning)
- Ogni risposta in Chainlit offre pulsanti di feedback nativi: **"Corretto"**, **"Sbagliato"**.
- Se l'utente clicca "Sbagliato", gli viene chiesto un commento (opzionale). Il feedback viene salvato strutturato nel DB (query originale, contesto recuperato, fonti, e commento).
- Le correzioni andranno ad alimentare i set di addestramento e i log MLOps per la valutazione delle regressioni.
- *Moderazione:* I feedback degli utenti entrano in una coda di "Admin Quarantine". Un sistemista deve approvarli prima che modifichino le policy dell'AI o vengano aggiunti al Golden Dataset.
