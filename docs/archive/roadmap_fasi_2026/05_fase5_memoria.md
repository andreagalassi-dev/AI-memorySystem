# Fase 5 — Memoria a Lungo Termine Multi-Utente

> **Dipende da:** Fase 4 (UI funzionante con auth per identificare l'utente)
> **Obiettivo:** Creare un sistema che ricordi nel tempo le preferenze e lo storico delle richieste di ogni utente e/o azienda cliente, abbattendo al contempo i costi/token di inferenza (addio "amnesia" dei chatbot standard).

## 5.1 Piramide Semantica a 4 Livelli (TencentDB Agent Memory)
L'integrazione della memoria avviene sfruttando l'innovativa architettura *Agent Memory* sviluppata da TencentDB (open source). A differenza del "buttare tutto lo storico chat nel contesto" (che spreca token o sfora il limite di RAM), il sistema distilla i ricordi in 4 livelli:

- **L0 — Conversazione (Raw Logs):** Salvataggio passivo e grezzo dell'intera sessione di chat. Resta su disco.
- **L1 — Fatti Atomici:** In background, un modello estrae informazioni precise. (Esempio: *"L'utente C.Rossi gestisce la flotta di compressori Atlas Copco GA-110"*).
- **L2 — Scenari / Tematiche:** Raggruppamento dei fatti L1. (Esempio: *"Manutenzione programmata Atlas Copco: ricerca ricorrente di filtri aria e kit valvole ogni 3 mesi"*).
- **L3 — Profilo Persona (Il Distillato):** Un profilo aziendale/utente estremamente compatto (es. 200 token) che racchiude l'essenza delle sue necessità.

## 5.2 Implementazione e Iniezione Dinamica
- **Stack Locale:** SQLite (con estensione vettoriale `sqlite-vec`) gestirà questa piramide nativamente durante la validazione locale, senza dipendere dal cloud. Successivamente, verrà passata su PostgreSQL.
- **Multi-Tenant (Sicurezza Dati):** Un layer rigido nel DB `core.tenants` assicura che un'azienda non possa mai recuperare o "ricordare" per sbaglio dati di un'azienda competitor che usa lo stesso server.
- **Iniezione (Zero Latenza):** Il vero vantaggio architetturale è che il livello L3 (e i Fatti L1 rilevanti per l'ultima query) vengono caricati dinamicamente **direttamente nel System Prompt** (il set di istruzioni base dell'AI) prima dell'inferenza. Il modello, dunque, "conosce" intrinsecamente il cliente prima ancora che questo finisca di salutare, non deve attivare un tool o fare una ricerca a latere per ricordarsene.
