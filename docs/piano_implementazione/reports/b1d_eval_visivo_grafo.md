# Report Audit Ramo Visivo Grafo (B1.d)

## Contesto e Metodologia

Questo report analizza le prestazioni del **ramo VISIVO del grafo** (`visual_node`), misurandolo in isolamento tramite `answer()` e la configurazione `session_deps`.
Il test set è `eval_visual_v0.jsonl` (15 query).

Diversamente dal benchmark storico (che usava `scripts/evaluate_visual.py`), il ramo del grafo in produzione si distingue per:
1. **Nessun Reranker**: la pipeline visiva nel grafo è *pointer-only* e restituisce direttamente le pagine recuperate dal vector db, ottimizzando la latenza ed evitando OOM su GPU.
2. **Access Control Reale (RBAC)**: le query sono state testate con il ruolo associato al documento atteso, non un profilo demo onnipotente.
3. **Top-K ridotto**: `visual_search` integrata nel grafo estrae meno candidati (le misurazioni confermano tipicamente 3 candidati rispetto alla profondità di 10 del reranker storico).

## Metriche Complessive

I risultati mostrano le metriche di retrieval per la top-k delle pagine restituite dal grafo:

| Metrica | Ramo Grafo (Senza Reranker) | Storico (Con Reranker) |
|---|---|---|
| **Recall@1** | 0.333 (5/15) | - |
| **Recall@3** | 0.533 (8/15) | - |
| **Recall@5** | 0.533 (8/15) | 0.400 (6/15) |
| **MRR**      | 0.400 | - |

> [!NOTE]
> Il tetto della Recall@5 è identico alla Recall@3 semplicemente perché il tool visivo nel grafo restituisce tipicamente un massimo di 3 candidati.

**Latenza Median (p50)** misurata a modelli caldi (residente in memoria): **0.212 secondi**, che conferma il vantaggio in efficienza di non usare un reranker MLLM real-time.

## Dettaglio per Lingua

| Lingua | Query (N) | Recall@5 | MRR | Note |
|---|---|---|---|---|
| **ES** | 1 | 1.000 | 1.000 | Trituradora Panciroli: query perfetta, Trovato al Rank 1 |
| **DE** | 1 | 1.000 | 0.333 | NordCap Kühltruhe: trovato al Rank 3 |
| **EN** | 3 | 0.667 | 0.667 | Kids MICRON sweeper, Gehl foot valve al Rank 1. Fallita "Branson tractor wiring" |
| **IT** | 10 | 0.400 | 0.267 | Tasso di successo più basso dovuto ad alcune sfide di similarità visiva-lessicale e al k limitato |

## Analisi dei Casi Rossi (Miss)

- **V002 ("esploso del rilascio frizione Branson 4520")**:
  - *Attesa*: p. 68
  - *Restituito*: p. 94, 69, 67
  - Il modello recupera le pagine adiacenti (67, 69) ma salta la 68 nella finestra stretta (top 3) senza l'allineamento finale del reranker.

- **V003 ("electric wiring diagram of the Branson tractor")**:
  - *Attesa*: p. 127
  - Il motore visivo "puro" non ha recuperato la pagina attesa; questo suggerisce un disallineamento tra il testo esatto "electric wiring" e l'embedding multivettore per lo specifico documento senza un ponte semantico forte.

- **V004 ("wiring harness dello chassis del Gehl V270")**:
  - *Attesa*: p. 94
  - *Restituito*: p. 54, 96, 126
  - Recupera pagine adiacenti/correlate. Il layout dei circuiti elettrici è visivamente molto simile da pagina a pagina; distinguere "chassis" richiede RAG testuale o un reranker test-image profondo.

- **V006 ("esploso del controller del motore Kohler industriale")**:
  - *Attesa*: Kohler industriale p. 24
  - Restituisce diagrammi di "Kohler marine" invece di "industriale". La componente visiva confonde i due manuali della stessa famiglia Kohler, rendendo critico il matching semantico esatto che si perde senza un reranker testo-testo robusto a valle.

- **V007 ("spaccato interno della pompa Iwaki")**:
  - Pur potendo accettare molte varianti del documento `iwaki_TC-X`, i candidati visivi primari sono andati su altre pompe ("hydrogear", "edwards_booster"). Il termine "spaccato" in combinazione visiva non è bastato a isolare "Iwaki".

- **V009 ("esploso del forno KBS gastronomia")**:
  - Trova forni correlati (Mamoli, DirectKitchen), indicando l'ottima capacità dell'encoder di riconoscere "l'estetica del forno/cucina", ma non discrimina semanticamente il brand specifico.

- **V011 ("esploso della trinciatrice Benassi T623")**:
  - Recupera la p. 1 (copertina) invece delle tavole interne attese, assieme ad altre macchine pulitrici.

## Conclusioni
L'implementazione attuale ottiene risultati persino superiori al benchmark storico (Recall@5 del 53.3% contro il 40% del ramo valutato in precedenza con reranker limitato al profilo demo).
Ciononostante, il cap a 3 risultati impedisce di raggiungere metriche più elevate; senza un reranking intermedio, query molto specifiche basate su dettagli testuali del componente (es. "chassis" rispetto a un'altra zona del veicolo, o un brand specifico rispetto a modelli visivamente uguali) restano "red" cases che potrebbero beneficiare di espansione query, metadati ibridi (sparse/BM25) da integrare all'embedding visivo Qwen.

## Verifica del reviewer (20/07/2026)

Tutto riprodotto coi tool: metriche ricalcolate dal raw JSON e coincidenti (complessive e
per lingua); spot-check dal vivo di V005 (hit@1 su gehl 167) e V006 (miss, restituisce
kohler_marine) → candidati **identici** al JSON; narrative dei casi rossi verificate una a
una contro `data/eval/eval_visual_v0.jsonl` e i candidati raccolti (fedeli); commit
separati (script+test / run+report); suite 747/2; tree pulito.

**Il cap a 3 è una costante di configurazione, non un limite del modello:**
`VISUAL_K = 3` in `src/graph/tools/visual_tool.py:29` ("poche pagine, alta precisione: è
l'occhio umano a scegliere"). Conseguenza diretta: recall@5 ≡ recall@3 per costruzione, e
9 pagine attese su query multi-pagina (V007 iwaki, V011 benassi, V012 panciroli, V014
rimoldi) competono per soli 3 slot. È la leva più economica dell'epica E: alzare VISUAL_K
(e/o reintrodurre un reranker sul ramo del grafo) va misurato PRIMA di interventi
sull'embedding. Nota: 5 delle 7 query rosse cadono su documenti con la pagina attesa
comunque presente nell'indice (answerable) ma non nei primi 3 — quindi è ranking/profondità,
non copertura d'indice.

**Caveat metodologico per il gate B2:** il ruolo è preso per-doc da `document_roles`
(tutte `tecnico` su questo set) — corretto, ma il set non esercita la dimensione RBAC
(nessun documento a ruolo ristretto tra gli attesi). Il golden gate visivo andrà esteso
con almeno una query il cui atteso sia RBAC-filtrato per un ruolo, per non lasciare quel
percorso non coperto.
