# Fase 1 — Pipeline a Doppio Binario (Ingestion Separata)

> **Dipende da:** P1 (Gate Ingestion superato), P2 (Retrieval ibrido funzionante)
> **Obiettivo:** Separare nettamente i due scopi dell'ingestion — cercare (Retrieval) vs estrarre dati strutturati (Data Entry).

L'intuizione architetturale alla base del sistema è che usare un solo modello per due task opposti crea colli di bottiglia e allucinazioni. Si adotta una Pipeline di Ingestion a **triplo binario sequenziale con Fast-Dispatch**:

## 1.0 Fast-Dispatch (Puro Testo)
- **Classificazione iniziale:** Un micro-step (basato su analisi veloce del PDF) verifica se il documento è "born-digital" e se la pagina contiene solo testo piano (es. introduzioni, indici, normative, avvertenze di sicurezza).
- **Estrazione rapida:** In caso positivo, il testo viene estratto istantaneamente tramite librerie ultra-leggere (es. `PyMuPDF`), bypassando completamente i modelli AI pesanti (PixelRAG e DeepSeek-OCR).
- *Vantaggio:* Risparmio enorme di compute e VRAM (elaborazione istantanea per gran parte del documento).

## 1.1 Binario Ricerca/RAG (PixelRAG)
- Tutti i PDF contenenti grafica vengono ingeriti visivamente da PixelRAG: ogni pagina è renderizzata in tile screenshot ad alta risoluzione.
- I vettori visivi (generati tramite Qwen3-VL-Embedding) vengono caricati nel database vettoriale Qdrant.
- Il Chatbot per rispondere alle domande visive (esplosi, schemi tecnici) userà **esclusivamente** questo database per avere il contesto perfetto della pagina originale.
- **Il retrieval testuale/BM25 resta attivo in parallelo** per le query su codici alfanumerici (`AB-12345-X`) — la ricerca ibrida dense/sparse è indispensabile.

## 1.2 Binario Estrazione Massiva Dati (DeepSeek-OCR-2)
- Gli stessi PDF (soprattutto le pagine identificate come listini, tabelle e griglie) vengono processati da DeepSeek-OCR-2 (3B parametri).
- Lo scopo *non* è rispondere alle domande della chat, ma estrarre righe CSV/JSON strutturate per popolare direttamente il database relazionale (PostgreSQL).
- **Enforcement dello Schema (Enrichment):** L'estrazione viene supportata da framework come **PydanticAI** o **Instructor**. DeepSeek è obbligato a restituire un JSON che rispetta un contratto preciso, ad esempio:
  ```json
  {
    "codice_originale": "V0-123",
    "descrizione": "Valvola Pressione",
    "prezzo": 12.50
  }
  ```
  Se il JSON è malformato, il framework ritenta in automatico. Questo garantisce che PostgreSQL riceva solo dati puliti.
- **Gestione Layout Complessi:** Celle unite, tabelle multi-pagina, e intestazioni ripetute vengono gestite automaticamente dal *Visual Causal Flow* di DeepSeek.

## 1.3 Il Cervello Unificatore (LLM)
- **Modello Primario:** Google Gemma 4 12B/26B MoE o NVIDIA Nemotron-Cascade-2.
- L'LLM funge da direttore d'orchestra, ricevendo i task dal grafo LangGraph:
  - Sbircia le immagini trovate da PixelRAG per le domande puramente visive.
  - Interroga le righe del database lette da DeepSeek-OCR-2 tramite un agente **Text-to-SQL** per rispondere a domande su codici esatti, prezzi o quantità.
- *Architettura del Router:* Il nodo "Router" di LangGraph classifica la query dell'utente ("vuoi vedere un pezzo" vs "quanto costa questo codice") e instrada l'esecuzione allo strumento corretto, fondendo poi i risultati nella risposta finale.
