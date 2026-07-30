# Epica C — Ingestion unificata del corpus (design)

> Data: 2026-07-30 · Autore: reviewer (brainstorming con utente) · Stato: approvato dall'utente, pre-piano.
> Contesto vivo: `docs/piano_implementazione/15_piano_prodotto.md` §C. Principio: CLAUDE.md
> ("giusto oppure non lo so, mai sbagliato"; guardrail non-LLM; metriche per lingua IT/EN/DE).

## 1. Problema (misurato coi tool, 30/07)

Copertura incrociata sui **142 PDF** del corpus nei tre binari di retrieval:

| Binario | doc coperti | popolato da | target |
|---|---|---|---|
| Strutturato | 46 | `src/ingestion/run_massive_extraction.py` (Docling) | `estrazione.righe_listino` (Postgres) |
| Narrativo | 54 | `src/ingestion/ingest_one.py` (chunking) | Qdrant `aike_chunks` |
| Visivo | 51 | `scripts/reindex_pixelrag.py` | Qdrant `aike_pages_visual` |

**Numeri chiave**: in almeno 1 binario = 80; **invisibili (in NESSUN binario) = 62**; in tutti e 3 = **19**.
I numeri 46/54/51 sono sottoinsiemi diversi: l'ingestion è stata eseguita **a pezzi**, con i tre entry-point
lanciati ad hoc su gruppi diversi di documenti. **Non esiste un orchestratore che faccia i tre binari per
documento** → causa-radice della copertura incoerente. Artefatti: `output/audit/b1_census_coverage.json`
(censimento tipo-doc), `output/audit/c_dispatch_validation.json` (validazione dispatch).

## 2. Il dispatch automatico è affidabile (validato read-only)

`page_classifier` + `fast_dispatch` + `table_detect` classificano **per pagina** (testo / grafica / dual /
scansione). Validazione su tutti i 62 invisibili (1050 pagine campione): 88% testo, 11% grafica, 8% dual
(→ anche visivo), 3% no-text-layer (→ OCR). Instradamento **sensato a tappeto**, nessun doc gravemente
mal-instradato. → **ci si può fidare** a girare l'ingestion su tutto il corpus. Il "tipo di documento"
emerge dai contenuti di pagina, non da un'etichetta manuale: una brochure non diventa un listino fasullo
(estrae solo le pagine-tabella, se ci sono).

## 3. Obiettivo

Un'**ingestion unificata, idempotente e completa del corpus**: per ogni PDF, girare i tre binari lasciando
decidere i classificatori automatici. Portare gli invisibili da **62 → ~0** e i pienamente-ingeriti da **19
→ ~intero corpus**. Verificare prima, promuovere in produzione con snapshot.

## 4. Architettura — l'unico pezzo nuovo

`ingest_document_full(pdf_path, role) -> IngestReport` che **compone i tre entry-point esistenti** (riusa,
non riscrive logica):

1. **Strutturato**: percorso `run_massive_extraction` con `AIKE_TABLE_EXTRACTOR=docling` → `righe_listino`.
2. **Narrativo**: `ingest_one.ingest_document(...)` → chunk+embedding → `aike_chunks`.
3. **Visivo**: entry-point pixelrag (`src/retrieval/pixelrag/ingest.py` / `reindex_pixelrag`) → `aike_pages_visual`.

Invarianti (tutte già presenti nei sotto-componenti, da preservare nell'orchestratore):
- **Idempotenza**: upsert con id deterministici; delete-before-upsert per doc_stem (nessun orfano, nessun
  duplicato al ri-ingest). Transazione atomica per-documento sul binario strutturato.
- **Additività**: mai `drop` globale.
- **Fail-closed per-binario**: se un binario fallisce su un doc, si logga lo scarto con motivo e si prosegue
  con gli altri; il documento non resta a metà in modo silenzioso (report esplicito).
- **RBAC/versioning**: payload `ruoli_consentiti`, `version_status='current'`, `doc_stem`, `page` propagati
  come già fa `ingest_one`.

`IngestReport` per documento: righe strutturate, chunk narrativi, pagine visive, scarti-con-motivo per
binario, tempi. È l'input della verifica meccanica.

## 5. Rischio e isolamento

I 62 invisibili sono **assenti da ogni binario** → ingerirli è **puramente additivo** (nuovi `doc_stem`, non
tocca dati esistenti): non può corrompere la produzione corrente. Difese comunque obbligatorie prima della
promozione: **snapshot `pg_dump -Fc -n estrazione`** + snapshot logico Qdrant (o consapevolezza che è
additivo per doc_stem nuovi) + **golden gate verde** + **ricollaudo demo** (worktree `788c49b` INTOCCABILE).
Lo spike (C.2) gira su un campione e viene verificato prima di qualunque giro a tappeto.

## 6. Verifica (scelta utente: meccanica + spot-check)

Per ogni doc: conteggi per binario (righe/chunk/pagine), plausibilità (densità prezzo attesa vs ottenuta,
% non-null), scarti-con-motivo. I doc con 0/garbage su un binario vengono **segnalati** (probabile "non è un
listino" → nessuna riga strutturata, ma resta il narrativo/visivo — comportamento corretto). Il **reviewer**
adjudica 2-3 crop per doc vs PDF. **Golden gate verde** + ricollaudo demo obbligatori prima di chiudere ogni
punto (da B2 in poi).

## 7. Punti (ordine di esecuzione)

- **C.1** — `ingest_document_full` orchestratore + unit test (esecutore, TDD rosso-prima). DoD: compone i 3
  entry-point; idempotente (ri-ingest dello stesso doc non duplica); fail-closed per-binario con report;
  gira su 1 doc di prova in **sandbox** (schema/collezioni temp) senza toccare produzione.
- **C.2** — Spike su ~5 invisibili di tipo diverso (listino / brochure / manuale / catalogo-esplosi / uno con
  scansione) in sandbox → `IngestReport` + verifica meccanica + spot-check crop (reviewer). DoD: instradamento
  corretto per tipo, conteggi plausibili, 0 garbage promuovibile.
- **C.3** — Con **OK utente + snapshot**: giro su tutti i **62 invisibili** → produzione (additivo). Golden
  gate verde, ricollaudo demo, ri-misura copertura (invisibili → ~0).
- **C.4** — Riconciliare i **parziali** (26 solo-strutturato, 2 solo-narrativo, ecc.) al pieno con lo stesso
  orchestratore (idempotente: ri-processa solo i binari mancanti).
- **C.5** — Percorso **OCR** per i ~3-4 scan (`no_text_layer`): GLM-OCR / VisionOcr → gli stessi tre binari.

## 8. Fuori scope (YAGNI)

Niente ri-estrazione dei doc già coperti nella prima passata (salvo C.4); niente riconciliazione relazionale
Fase-2; niente nuova logica d'estrazione (Docling è già la scelta dell'arco D-B); niente auto-detector di
code-family; niente UI/dashboard di risoluzione umana (promessa della visione, ma epica separata).

## 9. Interfacce / contratto

- `ingest_document_full(pdf_path: str, role: str, *, schema: str = "estrazione", chunks_collection: str =
  COLLECTION, visual_collection: str = "aike_pages_visual") -> IngestReport` — i parametri target permettono
  il **sandbox** (schema/collezioni temp) senza duplicare codice.
- `IngestReport` (dataclass): `doc_stem`, `structured_rows`, `narrative_chunks`, `visual_pages`,
  `scarti: dict[branch, list[reason]]`, `timings: dict[branch, float]`.
- Nessun `answer()` nell'ingestion: è popolamento puro. Le metriche di risposta restano all'eval ampio.
- **RBAC**: `ruoli_consentiti` per i nuovi doc si assegna con lo **stesso meccanismo già esistente**
  (profilo brand / registry usato da `ingest_one` oggi), NON inventato qui. Se un doc non ha profilo, il
  default è il ruolo ad accesso più ampio (`tecnico`) — coerente con lo stato attuale (tecnico vede 59/59).
  Da confermare nel piano guardando come il corpus attuale popola `ruoli_consentiti`.
