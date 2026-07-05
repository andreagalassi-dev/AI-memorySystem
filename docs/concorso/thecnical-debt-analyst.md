# Analisi del Debito Tecnico e Limiti Architetturali (Fasi P0-P5)

Questo documento traccia in modo trasparente tutti i limiti tecnici, le scorciatoie (PoC) e i "debiti" accumulati durante lo sviluppo del motore fino alla Fase 5. Sono problemi reali che emergeranno durante l'uso in produzione e che andranno risolti nelle Fasi 6 e 7 (SaaS).

## 1. Overfitting dell'Ingestion (Il problema "Mamoli")
> [!WARNING]
> **Gravità: Alta** - Blocca l'ingresso di nuovi clienti in autonomia.

* **Il Problema:** L'estrazione dei codici ricambio in Fase 1 è stata validata sul worst-case (Mamoli). Per superare il gate, il normalizzatore (`scripts/normalize_codes_docling.py`) è stato scritto con regole *hardcoded* (regex) ottimizzate per trovare i codici Mamoli (es. che iniziano con `V0` e hanno lunghezza fissa).
* **Impatto:** Se si carica il catalogo di un nuovo brand (es. Bosch), il parser fallirà nell'isolare e normalizzare i codici esatti, facendo crollare tutto il castello del RAG ibrido.
* **Soluzione (P7):** Slegare le regole di normalizzazione dal codice Python. Creare tabelle su PostgreSQL per definire i "Profili di Ingestion" per-cliente/per-brand, in modo che le regex siano caricate a runtime.

## 2. Rigidità sul Linguaggio Naturale (BM25 vs Semantica)
> [!WARNING]
> **Gravità: Alta** - Peggiora la User Experience dei tecnici meno esperti.

* **Il Problema:** Il motore ibrido fa un affidamento vitale sulla ricerca lessicale (BM25) per non sbagliare i codici alfanumerici. Questo però penalizza il linguaggio naturale. Se un utente cerca "piston pin Rotax" e il manuale è in italiano ("spinotto pistone"), il BM25 dà score 0. La ricerca vettoriale potrebbe trovarlo, ma non genera abbastanza confidenza, causando un rifiuto (livello BASSA).
* **Impatto:** Frequenti risposte "Non ho abbastanza evidenza" su query puramente testuali o cross-lingua.
* **Soluzione (P7):** Introdurre un nodo di **Query Expansion** o un dizionario dei sinonimi tecnico. Prima della query a Qdrant, la stringa viene espansa (es. `["piston pin", "spinotto pistone"]`), permettendo al BM25 di colpire il bersaglio.

## 3. L'Ambiguità dei Duplicati Intra-Doc (Margine 0.00)
> [!NOTE]
> **Gravità: Media** - Comportamento sicuro, ma frustrante.

* **Il Problema:** Se in una tabella lo stesso codice ricambio compare su 3 righe diverse (magari per varianti di colore diverse), il retrieval pesca 3 risultati identici con lo stesso identico punteggio BM25. Il calcolatore di confidenza valuta il "margine" tra il 1° e il 2° risultato. Essendo identici, il margine è zero. Il motore va in panico, dichiara ambiguità e blocca la risposta.
* **Impatto:** Il tecnico si vede negare la risposta per un pezzo che in realtà il sistema ha trovato perfettamente.
* **Soluzione (Fase Futura):** Modificare il calcolatore di confidenza implementando un *group-by* (raggruppamento) per codice ricambio primario prima di calcolare i margini.

## 4. Fallimenti Silenziosi dell'OCR (Docling)
> [!CAUTION]
> **Gravità: Media ma critica sui layout ostili.**

* **Il Problema:** Parsing di tabelle affiancate, righe senza bordi o celle unite in PDF particolarmente malformati. Docling a volte "fonde" le stringhe o salta le righe.
* **Impatto:** Se l'OCR si mangia il codice ricambio e non lo mette nella chiave vettoriale di Qdrant, il Guardrail Deterministico bloccherà a monte qualsiasi tentativo dell'LLM di inventarlo. La riga è di fatto invisibile.
* **Soluzione:** Migliorare il preprocessing geometrico delle tabelle prima dell'OCR. Inserire un tolleranza controllata (Fuzzy Matching a 1 carattere) nel normalizzatore solo come ultima ratio.

## 5. Cecità Gerarchica (Il problema degli "Assembly")
> [!NOTE]
> **Gravità: Bassa** - Limita i casi d'uso avanzati.

* **Il Problema:** Il database memorizza chunk indipendenti (singole righe o paragrafi). Non sa cos'è un "Kit" o un "Assembly".
* **Impatto:** Il sistema è perfetto per rispondere a "dammi il codice di X", ma fallisce se gli si chiede "elencami tutti i componenti inclusi nel Kit Frizione Y", perché fatica a raggruppare righe scollegate semanticamente ma gerarchicamente unite nel catalogo.
* **Soluzione:** Passare a un'architettura dati a "Grafo Reale" (Knowledge Graph) dove i vettori sono collegati da archi di parentela genitore-figlio.

## 6. Il Mock dello Stock
> [!TIP]
> **Gravità: Nulla per il PoC, Bloccante per la Produzione.**

* **Il Problema:** Al momento la giacenza è finta, generata tramite una funzione crittografica deterministica (hash) nella UI Streamlit per dare un'illusione di consistenza.
* **Soluzione (Integrazione ERP):** Implementare un Job notturno che sincronizzi (in sola lettura) codici, quantità e prezzi dall'ERP aziendale al database PostgreSQL, per poi interrogare Postgres durante il retrieval.

## 7. Frizione nell'Onboarding dei Nuovi Documenti
> [!WARNING]
> **Gravità: Alta** - Rallenta l'adozione da parte del cliente e trasforma il prodotto in consulenza.

* **Il Problema:** Attualmente, per inserire un nuovo documento, è necessario spostare il PDF nella cartella `corpus/` ed eseguire manualmente in sequenza 5 o 6 script Python da riga di comando. Non esiste un processo automatizzato e *user-friendly* per caricare nuovi listini o cataloghi.
* **Impatto:** Un utente business non può aggiornare il proprio database autonomamente. 
* **Soluzione (Fase Futura):** Sviluppare un sistema di **Auto-Ingestion a Cartella (Watchdog) o Drag & Drop**. La soluzione ideale prevede il monitoraggio automatico di una cartella (es. cartella di rete, SharePoint o drive locale). Quando l'utente ci trascina dentro una cartella di file PDF, un demone in background li rileva, lancia automaticamente l'intera pipeline di ingestion (estrazione, chunking, vettorizzazione) e li rende ricercabili aggiornando il registro PostgreSQL in totale autonomia. In alternativa, creare un pannello amministrativo nella Web App con funzionalità Drag & Drop per l'upload, in pratica una cartella di rete/disco contenente moltissimi file di qualsiasi tipo e ci deve essere un demone che estrae tutti file che ci interessano e procede con ingestione, ovviamente questi file possono venire eliminati, aggiornati, ed aggiunti, ma da quando il nostro sistema entra online in quella cartella lui si deve ricordare anche dei pdf eliminati, avendo una copia sua.

