# Fase 2 — Esportazione Massiva Incrociata (Catalogo + Listino)

> **Dipende da:** Fase 1.2 (DeepSeek-OCR-2 funzionante e validato)
> **Obiettivo:** Unire le informazioni lette dai cataloghi grafici (esplosi) con i dati letti dai listini prezzi (griglie) in un database relazionale unico con codici normalizzati.

## 2.1 Funzione di Merge e Cucitura Relazionale (SQL)
- Script che unisce il **Database dei Prodotti** (estratto dai cataloghi visivi) con il **Database dei Prezzi** (estratto dai listini testuali) sfruttando il **Codice Normalizzato**.
- Il codice normalizzato è prodotto dalla pipeline di normalizzazione già esistente (`src/ingestion/codes.py`). *Esempio: "0US-123.A" viene normalizzato in "0us123a"*.
- **Linker Relazionale:** Essendo PostgreSQL la *Single Source of Truth*, la "cucitura" tra il binario strutturato (i dati di listino) e quello visivo (PixelRAG) avviene tramite **SQL JOIN** native.
- Non viene introdotto un DB a grafo (GraphRAG) per evitare overhead: il codice normalizzato funge da chiave esterna universale tra la riga di database (il prezzo) e il file/tile screenshot associato (l'immagine su Qdrant), azzerando le latenze.

## 2.2 Gestione Errori e Scarti (Dashboard)
- Durante il merge automatico (es. 10.000 righe di listino), alcune righe risulteranno incerte o orfane (un prezzo senza un prodotto nel catalogo, o viceversa).
- Le righe incerte vengono separate in un log/tabella **"Scarti da verificare"** che include:
  - Il codice originale e il codice normalizzato tentato.
  - La pagina e il PDF sorgente (con link diretto per la verifica visiva).
  - Il motivo dell'incertezza (codice non trovato nel catalogo, match ambiguo, prezzo mancante o anomalo).
- **Dashboard Operativa:** Nella UI (Streamlit o Chainlit), l'amministratore avrà una dashboard dedicata per la revisione manuale degli scarti. Con un solo click (es. "Forza Associazione"), l'umano può risolvere i casi limite.
