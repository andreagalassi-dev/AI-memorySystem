# P2.7 — Pipeline multimodale ColPali (retrieval visivo pagina-level)

> Data: 2026-07-03 · Collection `aike_pages_visual`: **882 pagine** (878 route
> P2.5 + 4 delta), multivettori ~755 patch × 128 dim, **MaxSim su DOT**,
> HNSW disabilitato (m=0, brute-force). Storage: 377 MB multivettori +
> 352 MB render PNG (asset per la UI). Tutti i numeri osservati da esecuzioni.

## 1. Stack finale

- **transformers NATIVO** (`ColQwen2ForRetrieval`, checkpoint
  `vidore/colqwen2-v1.0-hf`), su MPS bf16. `colpali-engine` RIMOSSO (vedi §3).
- Indicizzazione: 5.7–6.0 s/pagina, **RSS picco 1.5 GB** (l'implementazione
  nativa è molto più parca del primo run: 8.8 GB), checkpoint/resume
  per-pagina, **882/882 ok, 0 falliti, 88 min**.
- Router esteso: classe **`visual`** (marker IT/EN/DE/ES) che conserva
  l'eventuale code_token; `search_unified` = visiva (+ lessicale sul codice se
  presente) per le query visual, routed invariata per il resto.

## 2. Risultati eval visiva v0 (15 query pagina-level, draft-sintetico)

| configurazione | R@1 | R@3 | R@5 | MRR |
|---|---:|---:|---:|---:|
| colpali-engine (LoRA rotto, cosine) | 0.067 | 0.133 | 0.133 | 0.089 |
| transformers nativo + DOT | 0.133 | 0.200 | 0.333 | 0.205 |
| **+ doc-hint filter (finale)** | **0.267** | **0.533** | **0.600** | **0.417** |

## 3. Il bug trovato (e come): LoRA caricato a caso, in silenzio

L'eval iniziale era catastrofica con un pattern anomalo (pagine quasi-bianche
in cima a OGNI query). Ipotesi testate:
- ~~Cosine vs Dot~~ (la normalizzazione amplifica i patch vuoti): **falsificata**
  con test diretto — dot ≈ cosine, la bianca vinceva comunque.
- **Confermata**: `colpali-engine` 0.3.17 con transformers 5.12 dichiara
  compatibilità ma il LOAD REPORT mostrava i pesi LoRA del checkpoint
  `vidore/colqwen2-v1.0` come UNEXPECTED e i moduli destinazione MISSING
  ("newly initialized"): rinomina dei moduli Qwen2VL in transformers 5.x →
  **adapter di retrieval inizializzato random, senza alcun errore**.
  Prova: test bianca-vs-target — rotto: 16.02 vs 15.24 (bianca vince);
  checkpoint -hf nativo: 7.17 vs 13.47 (separazione corretta).
- Lezione (seconda della serie "fallimenti silenziosi di dipendenze", dopo
  EasyOCR in P2.6): **mai fidarsi di un load senza leggere il load report**;
  il test bianca-vs-target è ora il canone di verifica del canale visivo.
- Costo: primo run di indicizzazione (82 min) buttato e rifatto; la distanza
  è stata comunque migrata a **DOT** (lo scorer ufficiale ColPali: la norma
  dei patch è informativa).

## 4. Doc-hint filter (deterministico)

Il retrieval visivo è ancorato al **testo visibile** sulla pagina: gli esplosi
muti non contengono il brand — che però sta nel nome del documento, e il
tecnico la macchina la nomina. `doc_hint_filter`: token alfabetici ≥4 char
della query confrontati con gli stem dei documenti indicizzati → filtro
`MatchAny` sul payload doc; nessun match → ricerca globale. Effetto misurato:
Iwaki da fuori-top-10 a top-3 perfetta; MRR complessivo +0.212.

## 5. Lettura onesta dei 5 miss residui (V001/V002/V004/V009/V015)

Tavole **anonime** (senza testo-ancora utile) dentro documenti grandi
(branson: 79 pagine grafiche) interrogate in italiano su pagine con titoli
inglesi ("rilascio frizione" vs "CLUTCH RELEASE" stampato). Il canale trova
il documento giusto ma non sempre la pagina esatta. Non si aggiusta l'eval:
i rimedi veri sono a P3 — fusione testo↔visione (la riga ricambio testuale
ancora la pagina), captioning VLM dei cartigli/titoli figura, ed eventuale
upgrade a modello visivo multilingue.

## 6. Scoperto e rimediato: punto cieco del classificatore P2.5

L'answerability dell'eval ha smascherato pagine grafiche classificate
testuali: firma = raster moderato (img 0.10–0.45) con testo moderato
(150–310 char), più `get_drawings` che conta OGGETTI path (un esploso può
essere 54 path grandi). **91 pagine** nel corpus hanno la firma sospetta.
Rimedio evidence-driven: delta route con le sole pagine PROVATE grafiche
(4 indicizzate); ricalibrazione completa del classificatore → P2.8/manutenzione,
con le firme documentate qui.

## 7. Debiti e rinvii dichiarati

- Eval visiva v0 = 15 query draft (2 con numeri di pagina corretti in corsa:
  erano miei errori di lettura dei tag di labeling, documentati nel jsonl).
- Fusione testo↔visione, soglie "non lo so" visive, VLM sui callout: P3.
- Ricalibrazione classificatore (91 pagine firma sospetta): P2.8.
- Modello visivo multilingue / query translation: valutazione futura, non
  ammessa ora (P2 senza LLM).
