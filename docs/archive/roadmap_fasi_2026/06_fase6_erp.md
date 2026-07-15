# Fase 6 — Integrazione Gestionale ERP (Sola Lettura)

> **Dipende da:** Fase 2 (Merge cataloghi completato), Fase 4 (UI funzionante), e accordo sistemistico con il cliente.
> **Obiettivo:** Portare i dati reali, dinamici e transazionali dell'azienda (Stock a magazzino, prezzi personalizzati cliente, disponibilità) all'interno del flusso AI, senza *mai* rischiare di danneggiare il gestionale del cliente.

## 6.1 Sincronizzazione Notturna / Batch (Il Sync)
- **Logica Push/Pull:** A seconda dell'ERP del cliente (es. SAP Business One, Zucchetti, Danea, TeamSystem), si imposta un Job Cron o un listener webhook che esegue un export batch dei dati rilevanti.
- I dati sincronizzati si limitano strettamente a:
  - Codice articolo gestionale (per aggancio con il Codice Normalizzato del sistema).
  - Giacenza a magazzino / Impegnato.
  - Prezzi di listino base / Sconti per fascia cliente.
  - Stato dell'articolo (Attivo, Obsoleto, In Esaurimento).
- Questo export viene caricato sul server AI tramite operazione `UPSERT` (Insert on conflict Update) nelle tabelle del DB locale PostgreSQL.

## 6.2 Interrogazione dal Grafo (Il Semantic Layer)
- **Isolamento dell'ERP Originale:** Per ragioni assolute di stabilità e cyber-security, l'agente "Text-to-SQL" dell'AI **NON si interfaccerà MAI direttamente al database di produzione dell'ERP aziendale**. 
- **Le Viste Materializzate (Semantic Layer):** L'LLM interrogherà esclusivamente una replica (il layer semantico) interna a PostgreSQL. Questo layer è costituito da *Viste Materializzate* in cui le colonne astruse tipiche degli ERP (es. `TB_ART_01_GZ`) sono state tradotte in inglese o italiano colloquiale (es. `giacenza_magazzino_principale`), in modo che l'AI non fallisca la query SQL.
- Quando l'utente chiede: *"Quanti ne abbiamo a magazzino e quanto costa per il cliente Mario Rossi?"*
  - L'agente converte la domanda in SQL.
  - Interroga le viste locali (veloci, sicure, e read-only).
  - Formula la risposta e allega in coda un meta-dato fondamentale: *"Dati di stock aggiornati al: [Data ultima sincronizzazione]"*.
