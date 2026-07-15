# Watchdog — prova end-to-end (Fase B, step B5)

> **Cosa dimostra.** Il ciclo completo dell'auto-ingestion: **aggiungi → cerca →
> modifica → cerca → elimina → l'avviso "ELIMINATO" compare**, con codice di
> produzione (nessun mock). Mette insieme i fix di Fase A (A3 `reconcile`, A4
> cascata soft-delete) e l'auto-ingestion di Fase B (B2 ingestion per-documento,
> B3 pulizia anti-orfani, B4 Watchdog collegato).
>
> **Come riprodurlo:** `docker compose up -d postgres qdrant` e poi
> `uv run python scripts/watchdog_e2e.py` (exit 0 = ciclo dimostrato).

## Isolamento (perché non tocca la produzione)

Il registro di produzione ha già **57 documenti** e `aike_chunks` ~33k punti. Per
non mutarli, l'e2e gira **isolato**:

- schema Postgres temporaneo `watchdog_e2e` (creato e distrutto dallo script);
- collezione Qdrant dedicata `test_e2e_<random>` (creata e distrutta dallo script).

L'**unico** override è il *nome-collezione*, ripuntato alla collezione demo nei tre
moduli che lo leggono (`ingest_one`, `embedding`→`soft_delete`, `retrieval.search`).
Tutto il resto è codice di produzione: estrazione **Docling reale** (cappata a 2
pagine per velocità), embedding **BGE-M3 + BM25**, upsert/delete **Qdrant**,
versioning **Postgres**, cascata **`sync_collection`**, avviso **`reconcile`**.

> Nota: `answer(query, role)` (il wrapper completo con sintesi LLM via Ollama —
> presente in questo ambiente: `qwen2.5:14b`) è legato per costruzione alla
> collezione `aike_chunks` di produzione. L'e2e isolato esercita gli **stessi**
> componenti deterministici che `answer` racchiude — la **citazione** proviene dal
> retrieval e l'**avviso ELIMINATO** da `reconcile` — che sono la parte che dà la
> garanzia "giusto, oppure non lo so". La sintesi LLM aggiunge solo la forma
> linguistica, non la fonte né l'avviso.

## Esecuzione osservata (output reale)

Corpus di prova: `corpus/pumps/iwaki_TC-X031VTN.pdf` (v1) e
`corpus/pumps/iwaki_TC-X200SSN.pdf` (v2), copiati come `demo_ricambi.pdf` nella
cartella monitorata. Query: `"ricambi pompa"`, ruolo `tecnico`.

### 1. AGGIUNGI — un ciclo del daemon sul file nuovo
```
  diff rilevato: {'demo_ricambi.pdf': 'new'}
  tracked_files: status=active profile_warning=True document_id=1
  Qdrant: punti current del doc = 21
```
Il file passa a **`active`**; `profile_warning=True` (nessun profilo brand per la
pompa → `default` passthrough, coerente con Fase C); **21 punti `current`** in Qdrant;
`document_id` collegato (serve alla cascata di eliminazione).

### 2. CERCA — la risposta cita la fonte
```
  query='ricambi pompa' ruolo=tecnico
  citazione top-1: doc=demo_ricambi pagina=1 codice=None status=current score=0.5
```
Il retrieval trova il documento appena aggiunto e lo **cita** (documento + pagina).
`codice=None` perché la pagina 1 di questo datasheet è testo, non una tabella
ricambi con colonna-codice: il chunk resta comunque citabile (regola d'oro
rispettata — nessun codice inventato).

### 3. MODIFICA — re-ingest senza duplicati (B3)
```
  diff rilevato: {'demo_ricambi.pdf': 'modified'}
  punti PRIMA del re-ingest = 21, DOPO = 19  (niente somma/orfani: B3)
```
Sovrascritto con contenuto diverso, il diff è **`modified`**; dopo il re-ingest i
punti sono **19** (il conteggio dell'ultima versione, non 21+19): la pulizia
anti-orfani `delete_document_points` ha rimosso i chunk della versione precedente.

### 4. CERCA (post-modifica) — fonte ancora citabile
```
  citazione top-1: doc=demo_ricambi pagina=1 codice=None status=current score=0.5
```

### 5. ELIMINA — cascata soft-delete (A4)
```
  diff rilevato: {'demo_ricambi.pdf': 'deleted'}
  Qdrant dopo l'eliminazione: current=0 deleted=19
```
Rimosso il file, il diff è **`deleted`**. La cascata (`apply_soft_delete` →
`sync_collection`, fix A4) allinea Qdrant alla SSOT Postgres: **0 punti `current`**,
**19 `deleted`**. Senza il fix A4 i punti sarebbero rimasti `current` (il documento
eliminato avrebbe continuato a comparire come valido).

### 6. AVVISO ELIMINATO (A3)
```
  hit in 'current' = 0 | hit in 'deleted' = 5
  final_level = BASSA
  final_answer:
    Non ho evidenza sufficiente per rispondere.
    ATTENZIONE: risposta basata su un documento ELIMINATO dall'archivio. Questo
    documento non è più presente nella cartella monitorata. Le informazioni
    potrebbero essere obsolete o non più valide. Verificare con il fornitore o il
    responsabile tecnico.
```
Nello scope `current` non c'è più nulla; il fallback trova l'evidenza nello scope
`deleted` (5 hit) e `reconcile` (fix A3) **appende l'avviso ELIMINATO**, con livello
cappato a **BASSA**. È esattamente il caso che prima del fix non veniva mai
raggiunto (return anticipato su BASSA prima del blocco d'avviso).

## Esito

```
ESITO — ciclo completo dimostrato: tutte le asserzioni VERDI
EXIT CODE = 0
```

Il ciclo **aggiungi → cerca → modifica → cerca → elimina → avviso ELIMINATO** è
dimostrato end-to-end con evidenze riproducibili, senza lasciare traccia nella
collezione o nel registro di produzione. Questo chiude il cancello d'uscita di
Fase B.
