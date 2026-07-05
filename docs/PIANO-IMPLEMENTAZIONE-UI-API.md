# Piano di Implementazione: Verso la Produzione Locale (UI, API e ERP)

Questo documento traccia la roadmap finale per trasformare l'attuale core AI (completamente funzionante via riga di comando) in un sistema aziendale realmente usabile in locale da utenti non tecnici.

---

## 1. Costruzione API e Backend Sicuro (FastAPI)
L'architettura necessita di un layer di interscambio sicuro (API) che riceva le richieste dagli utenti, ne verifichi i permessi, e le passi al motore LangGraph esistente.

- **Endpoint di Login**: Creazione di un sistema di autenticazione JWT (o basato su token) per assegnare e verificare i ruoli (`tecnico`, `commerciale`, `amministrazione`, `admin`).
- **Endpoint Chat**: Un'API REST/WebSocket che accetti la query dell'utente, verifichi il suo ruolo, invoca `answer(query, role)` e restituisca la risposta strutturata.
- **Endpoint Upload**: API per caricare manualmente i PDF.
- **API di Amministrazione Profili**: Un set di endpoint (es. `POST /api/profiles`) per permettere a un amministratore di gestire le regole RegEx dei nuovi fornitori nel database.

## 2. Sviluppo Interfaccia Utente (Frontend)
L'interfaccia visuale dedicata ai tecnici e agli amministratori.

- **Scelta dello Stack**: React/Vue.js (o Streamlit come soluzione ponte rapida).
- **Finestra di Chat & Proof**: La chat deve mostrare la risposta dell'LLM (colore verde/giallo/rosso) fianco a fianco all'immagine del PDF.
- **Pannello Admin**: Una schermata "Impostazioni Fornitori" per aggiungere le regole dei brand con un'interfaccia guidata.

## 3. Esportazione Massiva Incrociata (Catalogo + Listino) [NUOVO]
Implementazione della funzione di estrazione di massa (bulk) e riconciliazione automatica.
- **Funzione di Merge**: Uno script che unisce in automatico il Database dei Prodotti (estratti dai PDF dei cataloghi) con il Database dei Prezzi (estratti dai listini), incrociandoli sul Codice Normalizzato.
- **Gestione Errori (< 2%)**: Le righe incerte vengono separate in un log "Scarti da verificare manualmente", per garantire zero inquinamento nel sistema gestionale finale.

## 4. Refactoring Architetturale Modelli AI [NUOVO]
A seguito della ricerca tecnologica, si progetta l'upgrade dell'intelligenza del sistema.

- **Motore OCR e Retrieval**: Sostituzione dell'attuale Docling con alternative State-of-the-Art:
  - **Chandra OCR 2 (Datalab)**: Eccezionale per layout complessi, note a mano e tabelle (4B parametri).
  - **DeepSeek OCR 2**: Imita la lettura umana (Visual Causal Flow), ideale per tabelle ostili.
  - **PixelRAG**: Si valuta il bypass totale dell'OCR testuale in favore di un recupero puro a livello di "pixel" (tramite Qwen3-VL-Embedding) per non perdere mai la struttura originale.
- **Motore LLM Chat**: Passaggio dall'attuale Qwen 2.5 14B a modelli più evoluti (se l'hardware lo consente):
  - **Google Gemma 4 (12B o 26B-MoE)**: Nativamente multimodale con enormi capacità di reasoning.
  - **Qwen 3.6 (27B)**: L'eccellenza per compiti logici.

## 5. Ciclo di Feedback (Active Learning)
- **Azione Utente**: Pulsanti "Corretto", "Sbagliato", "Manca informazione".
- **Database Logs**: Memorizzazione strutturata su PostgreSQL della Query originale, Contesto recuperato, e Feedback utente.

## 6. Integrazione Gestionale ERP (Sola Lettura)
Per abbandonare l'attuale generazione finta (mock) dei prezzi e delle giacenze.
- **Connettore Astratto**: Creazione di un'interfaccia `ERPConnector` standardizzata.
- **Sincronizzazione Notturna (Cron Job)**: Script che si collega al database dell'ERP locale e riversa lo stock aggiornato in tabelle apposite su PostgreSQL.
