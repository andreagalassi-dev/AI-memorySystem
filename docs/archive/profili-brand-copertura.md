# Report di Copertura dei Profili Brand

Questo documento fornisce una panoramica degli attuali "Profili di Ingestion" definiti all'interno del sistema (Fase C1), utili per normalizzare e riconoscere i codici dei ricambi durante la fase di ingestione OCR.

## 1. Profili Attualmente Supportati

Il sistema gestisce l'estrazione e normalizzazione per i seguenti brand, come censito nello script di seed (`config/brands.json`) e nel database (tabelle `ingestion_profiles`, `ingestion_code_families`, `ingestion_doc_map`).

| Brand | Famiglie Principali | Extract Pattern | Token Pattern (Canonico) | Riparazione Fuzzy | Note |
|---|---|---|---|---|---|
| **Mamoli** | `V0-lunga` | `V0(?:\s*_`*[0-9A-Z]){10}` | `^V0[0-9A-Z]{10}$` | Sì (`fuzzy_repair: true`) | Definisce anche `short_pattern`, `unsafe_numeric`, e `assembly_pattern` (`KIT|ASSEMBLY|ASSY|GRUPPO`). |
| **Electrolux** | `0US-pnc` | `0US[0-9A-Z]{3}` | `^0US[0-9A-Z]{3}$` | No | Solo codici PNC Electrolux. Cleanup esatto senza rejoin. |
| **DeliTools** | `numerico-9` | `\d{9}` | `^\d{9}$` | No | Codici esclusivamente numerici a 9 cifre. |
| **default** | *Nessuna* | *Nessuno* | *Nessuno* | No | Profilo di fallback con `passthrough=true`. Effettua cleanup sicuro ma non normalizza nulla. |

## 2. Campi Che Definiscono un Profilo

Un Profilo di Ingestion è composto da due entità relazionali su database (mappate anche nel JSON di seed):

1. **Configurazione Globale Profilo (`ingestion_profiles`)**:
   - `short_pattern`: Regex per intercettare codici corti sicuri.
   - `unsafe_numeric`: Regex per numeri sospetti privi di prefisso.
   - `passthrough`: Se attivo (es. per il `default`), lascia passare token che sembrano codici così come sono.
   - `fuzzy_repair`: Abilita/disabilita meccanismi di correzione logica su OCR danneggiati (es. `O` <-> `0`).
   - `assembly_pattern`: Regex per identificare intestazioni di distinte basi (BOM) come "KIT" o "ASSEMBLY".

2. **Famiglie di Codici (`ingestion_code_families`)**:
   Ogni brand può avere N famiglie. Ogni famiglia definisce:
   - `name`: Nome identificativo.
   - `extract_pattern`: Regex tollerante (inclusi eventuali spazi/errori) utilizzata in fase di estrazione testo/tabelle.
   - `token_pattern`: Regex ancorata (`^...$`) canonica usata dal Router e dal Chunker.
   - `rejoin`: Se i frammenti sparsi devono essere riuniti.
   - `restore_prefix` & `restore_run`: Configurazioni opzionali per la ricostruzione di prefissi perduti.

## 3. Procedura Attuale per l'Aggiunta di un Profilo

Attualmente, per l'onboarding di un nuovo brand, è necessario un procedimento manuale in 2 fasi:

1. **Modifica del Seed**: Editare manualmente il file JSON `config/brands.json`, aggiungendo il nuovo oggetto nella chiave `"profiles"` e le sue relative maschere per i file ("glob") dentro `"doc_map"`.
2. **Migrazione a Database**: Eseguire il seguente comando in un terminale affinché il contenuto JSON venga letto, validato come RegEx e caricato nelle tabelle di PostgreSQL:
   ```bash
   uv run scripts/seed_ingestion_profiles.py
   ```

*Nota: Questo meccanismo di aggiunta verrà presto rimpiazzato/affiancato da uno script interattivo e robusto (Task C2).*
