# Piano di Migrazione: Architettura Ibrida / Cloud-First

Questo documento definisce la roadmap futura per l'upgrade del sistema AI-memorySystem, passando dall'attuale architettura *100% Local-First* a un'infrastruttura *Cloud-Assisted* o *SaaS*. 

Il passaggio al cloud è previsto per il momento in cui l'azienda necessiterà di processare volumi di dati su scala industriale, accettando di inviare i PDF e le query a provider cloud esterni in cambio di prestazioni State-of-the-Art assolute e costi di infrastruttura locale nulli.

## FASE 1: Estrazione e OCR in Cloud (Sostituzione Pipeline Ingestion)
L'attuale elaborazione locale (Docling / ColPali) è intensiva per l'hardware. Nel futuro cloud, delegheremo il parsing visivo:
- **Mistral OCR API**: Utilizzo degli endpoint API di Mistral per l'estrazione perfetta di tabelle complesse e layout industriali, abbattendo i tempi di ingestion dai minuti ai secondi per file.
- **Vantaggio**: Zero consumo di GPU locale; scaling infinito (si possono ingerire 1.000 PDF contemporaneamente).

## FASE 2: Vettorizzazione Multimodale Unificata
L'architettura ibrida attuale (BM25 Lessicale + Vettore Denso + Vettore Visivo) verrà consolidata:
- **Google Embedding 2 (Gemini API / Vertex AI)**: Sostituzione dei modelli locali BGE-M3. Questo modello cloud prende nativamente in pasto Testo, Immagini e PDF creando un unico spazio vettoriale.
- **Vantaggio**: Non serve più mantenere logiche separate di chunking testuale e visivo. Il database Qdrant ospiterà un solo vettore potentissimo, semplificando il codice del 50%.

## FASE 3: Sintesi e Ragionamento LLM (Sostituzione Qwen Locale)
Il "cervello" della chat passerà dai modelli locali quantizzati ai grandi modelli proprietari accessibili via API:
- **Integrazione API**: Sostituzione del nodo LangGraph di sintesi per effettuare chiamate a **OpenAI (GPT-4o)** o **Google (Gemini 1.5 Pro / Gemini 2.0)**.
- **Vantaggio**: Capacità logiche enormemente superiori, finestre di contesto da milioni di token (capacità di analizzare interi manuali in tempo reale senza affidarsi solo al RAG).

## FASE 4: Storage e Database Cloud-Native
- **Qdrant Cloud**: Migrazione del vector DB dai container Docker locali ai cluster gestiti.
- **Supabase / Neon Postgres**: Spostamento del Single Source of Truth (PostgreSQL) su database serverless.
- **Sicurezza**: Implementazione di crittografia end-to-end e Data Residency (hosting garantito in server UE) per conformità GDPR sui dati industriali dei clienti.
