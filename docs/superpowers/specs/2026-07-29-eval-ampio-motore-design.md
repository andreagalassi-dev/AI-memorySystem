# Spec — Misura ampia del motore di ricerca ("eval ampio")

> Epica **B (Misura)** — espansione. NON è un'epica nuova.
> Origine: brainstorming reviewer+utente 2026-07-29. Decisione utente: partire dalla
> **misura** prima di completare copertura/esplosi/narrativo ("dubita della misura prima
> del sistema"). Ground truth = **ibrido** (verità-PDF adjudicata + check auto-derivati).

## 1. Problema e obiettivo

Il motore è **preciso** (gate `golden_gate` verde, 0 WRONG_ALTA) ma **incompleto**, e la
misura attuale è **stretta** (~40 query nel golden gate) — non dice *dove* il motore
fallisce. Per puntare a "errore quasi esente" serve prima una **fotografia ampia** che
misuri i 3 rami su tutte le lingue, con verità reale (non solo il plumbing).

**Obiettivo del punto:** avere i **primi numeri veri** per-ramo e per-lingua — recall@k,
correttezza-data-l'evidenza, tasso di "non lo so", correttezza di routing — su un set
stratificato, più un report di copertura del corpus. Questi numeri detteranno la tappa
successiva (copertura 83 doc / esplosi / narrativo), invece di indovinarla.

**NON obiettivo di questo punto:** migliorare il motore (copertura, recall, esplosi). Qui
si costruisce solo il *metro*. Il `golden_gate` resta il gate veloce anti-regressione;
questo eval è la fotografia ampia periodica, complementare.

## 2. Stato verificato coi tool (2026-07-29)

- Corpus su disco: **142 PDF**. Indicizzati: **59 doc** → **83 doc invisibili** al motore.
- Strutturato `estrazione.righe_listino`: 37431 righe / **46 doc** (arco Docling D-B.4 chiuso).
- Narrativo `aike_chunks`: 32976 punti / 59 doc. Visivo `aike_pages_visual`: 8371 pagine.
- `golden_gate`: verde 20 PASS_ALTA / 13 PASS / 8 MISS, 0 WRONG_ALTA. Per lingua:
  IT 24/30, EN 5/6, DE 4/4, ES 1/1. → hit-rate ~80% con headroom, misura stretta.

## 3. Architettura — tre pezzi isolati (dato ≠ codice ≠ report)

### 3.1 Set di verità stratificato (livello "verità") — DATO, lavoro reviewer
File versionato (JSON, stile `output/spike_b/goldenset/reviewer_truth.json`), es.
`output/eval/goldenset_v2/truth.json`. Ogni item mano-adjudicato dal reviewer **contro il
PDF (crop)**. Campi per item:
- `id`, `query`, `lang` (it/en/de/es), `branch` (structured|visual|narrative),
  `doc` (doc_stem atteso), `page` (attesa, se applicabile),
- `expected` (essenza risposta attesa: codice/prezzo, oppure descrizione/info),
- `kind`: `positive` | `negative` (per i negativi: `must_say_nonso=true`),
- `source`: `crop` (adjudicato a vista) | `agreement`.
Stratificazione: 3 rami × lingue × tipi-doc (listino / esploso / manuale / brochure),
doc diversi (non solo i 4 pilota). **Prima tranche ~30-40 item** per stare in piedi
subito; il set cresce nel tempo (SPEC §F.1: pavimento, non tetto).

### 3.2 Check strutturali auto-derivati (livello "idraulica") — rete anti-regressione
Generati da `righe_listino`: campione di codici per doc → query "codice X" → verifica che
il motore restituisca il valore DB. **Etichettati esplicitamente "plumbing, non verità"**:
catturano regressioni di pipeline, NON errori di estrazione (una riga garbage risulta
"corretta"). Inclusa una **coverage table**: per i 142 PDF, indicizzato sì/no per ramo.

### 3.3 Runner + report — CODICE, lavoro esecutore
Script (riusa `answer()` + `get_deps` come `golden_gate`) che esegue il set di verità e
calcola, **separati per lingua e per ramo**:
- **recall@k**: il `doc`/`page` atteso è nei top-k candidati del retrieval?
- **correttezza data l'evidenza**: la risposta contiene l'essenza attesa (codice/prezzo/info)?
- **tasso "non lo so"**: distinguendo *giusto* (negativi/fuori-corpus) da *pigro* (positivo
  mancato). I negativi che rispondono = falso positivo (grave).
- **routing**: ramo effettivo (dal router) vs `branch` atteso.
Output: report leggibile (tabella per lingua × ramo + coverage), salvato in `output/eval/`.

## 4. Definizione delle metriche (SPEC §F.1)

Due metriche **separate**, mai fuse: **recall@k** (retrieval ha trovato) e **correttezza**
(data l'evidenza giusta, risposta giusta). Più il **"non lo so"** come terza dimensione.
Tutto **per lingua** (una lingua debole non si nasconde in una media) e per ramo.

## 5. Fasi

- **Fase 1 (questo punto):** runner + report + check auto-derivati + formato del set di
  verità + **prima tranche ~30-40 item** (seed reviewer) → primi numeri ampi.
- **Fasi successive (fuori da questo spec):** crescita del set; poi le tappe motore che i
  numeri indicheranno (copertura / esplosi / narrativo), ognuna con proprio spec→piano.

## 6. Proprietà (chi fa cosa)

- **Set di verità** (§3.1): il **reviewer** adjudica i crop (come nell'arco Docling).
- **Runner/report/check auto** (§3.2-3.3): l'**esecutore**, prompt numerato, TDD rosso-prima,
  permessi file espliciti, DoD numerico, verifica reviewer di ogni claim coi tool.

## 7. Definition of Done (Fase 1)

- Esiste `output/eval/goldenset_v2/truth.json` con ≥30 item adjudicati (3 rami, ≥3 lingue,
  ≥8 doc diversi, con negativi).
- Il runner produce un report con recall@k, correttezza, tasso "non lo so", routing —
  **separati per lingua e per ramo** — + coverage table (142 PDF, indicizzato per ramo).
- Test rosso-prima sul runner (una metrica calcolata su input noto).
- `golden_gate` resta verde (nessuna regressione: l'eval non tocca produzione).
- Numeri consegnati e verificati dal reviewer coi tool (non fidarsi del report esecutore).

## 8. Rischi e note

- **Misura finta:** i check auto-derivati NON misurano la correttezza di estrazione →
  vanno sempre etichettati "plumbing". La verità reale sta solo nel set adjudicato.
- **Destabilizzazione:** l'eval è read-only, non tocca `righe_listino`/Qdrant/produzione.
- **Ollama:** il runner chiama il modello → stessa cautela del gate
  (`OLLAMA_HOST=http://localhost:11435`, `.venv/bin/python`, leggere il log non l'exit code).
- **Esecutore:** storicamente gonfia i numeri e fabbrica report → RAW obbligatorio + ri-verifica.
- **Fattibilità recall@k:** richiede la **lista dei candidati** del retrieval (top-k), non solo
  le citazioni finali. Da verificare in fase di piano se `answer()`/lo stato del grafo la
  espone già (il `golden_gate` usa `citations` + `check_index_fn`); se no, il primo task del
  piano è esporre i candidati in modo read-only, senza cambiare il comportamento del motore.
