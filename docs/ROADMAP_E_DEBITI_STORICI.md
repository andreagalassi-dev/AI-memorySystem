# Analisi Storica e Roadmap (Ex "Concorso")

Questo documento raccoglie in maniera ordinata e unificata le direttive storiche, lo stato di risoluzione dei vecchi bug e la roadmap per il completamento del sistema (precedentemente contenuti nella directory `concorso/`). 

Allo stato attuale, **tutti i debiti e bug storici (Fino a D2 compresa) sono stati risolti e testati con successo nel repository**.

---

## 1. Debiti Tecnici Storici (Tutti Risolti o programmati per D3)
*(Storico dei problemi emersi durante lo sviluppo P0-P5)*

1. **Overfitting Mamoli (Ingestion)**: *Risolto*. Separata la logica di parsing in profili salvati su Database (Regex flessibili per brand diversi).
2. **Linguaggio Naturale vs BM25**: *Risolto*. Introdotto nodo di "Query Expansion" semantica per affiancare il BM25 lessicale.
3. **Margine di Confidenza a 0.00**: *Risolto*. Implementato il group-by pre-margine, per non far fallire il motore quando trova righe identiche dello stesso codice.
4. **Cecità Gerarchica (BOM/Kit)**: *Risolto (Fase D1)*. Il grafo intercetta ora le Distinte Base (Assembly) ed espande dinamicamente la ricerca ai sotto-componenti usando il sistema degli `hop`.
5. **Silenzio del Watchdog**: *Risolto*. Il demone Auto-Ingestion è attivo e capace di processare o rimuovere (Soft Delete) i PDF caricati dagli utenti, agganciandosi alla pipeline reale.
6. **Stock/Giacenza Finta**: *Aperto (Programmato in Fase D3)*. I prezzi e le disponibilità attuali sono hash mockati. L'obiettivo futuro è l'integrazione ERP in sola lettura.

## 2. Revisione Bug Architetturali (Tutti Risolti)
*Elenco dei fallimenti di sistema riscontrati storicamente e ora completamente colmati.*

- **Test Versioning Rotto**: L'importazione obsoleta (`fallback_node`) è stata fixata e testata.
- **Avviso Obsoleto/Eliminato Ignorato**: Il sistema di `reconcile` è stato corretto e ora avvisa sempre se si risponde pescando da un PDF "superseded" (vecchio) o "deleted" (cancellato).
- **Watchdog Crash & Fallimento Silenzioso**: Standardizzato l'uso dei driver database (`psycopg3`), corrette le chiamate a `sync_collection` per sincronizzare Postgres e Qdrant senza omettere gli errori (Fail-closed).
- **Data Leak sull'Hop-2 (RBAC)**: *Risolto (Fase D1)*. L'espansione della distinta base ora eredita forzatamente il Ruolo dell'utente (Tecnico, Commerciale) bloccando ogni fuga di dati non autorizzata.

## 3. Roadmap Attuale (Piano Implementazione)

Il progetto prevede l'evoluzione del sistema da "motore di prova" a vero assistente aziendale completo, strutturato a Fasi:

### ✅ FASE D1: Gestione Distinte Base e Assembly
*(Completata)*
- Permette di rispondere a domande gerarchiche ("Quali sono i pezzi del Kit X?"). Eredita i ruoli di sicurezza, non allucina grazie ai guardrail e aggiorna la metrica `R_hierarchy` a testimonianza dell'avanzamento.

### ✅ FASE D2: Preventivatore Automatico (Bulk Quotes)
*(Completata)*
- Creazione dell'Agente Preventivatore. Riceve una lista destrutturata di richieste, e restituisce un CSV formattato.
- Usa lo stesso motore anti-allucinazione: se la confidenza sulla singola riga preventivata non è `ALTA`, marca esplicitamente la riga come `DA VERIFICARE` anziché vendere un ricambio errato.

### ⏳ FASE D3: Piattaforma B2B e Integrazioni
*(In via di definizione / Da iniziare)*
L'architettura entrerà a contatto diretto con il mondo esterno aziendale. Per poterla implementare sono necessarie precise decisioni di Business:
1. **Esposizione / Bot Sicuro**: Sviluppare il layer di Login (API/FastAPI) e definire policy conversazionali.
2. **Integrazione ERP**: Sincronizzazione notturna di prezzi e giacenze reali da un gestionale (es. SAP, Danea) su PostgreSQL.
3. **Multi-tenancy**: Separazione logica dei cataloghi in base all'azienda/cliente loggata, essenziale per la transizione verso il SaaS.
