# Report di Valutazione Qwen3-VL (Single-Vector) e Mitigation Plan

## 1. Risultati Base e Reranker
L'implementazione base del modello Qwen3-VL-Embedding (single vector, MRL 1024-d, prompt_name="query") unita al Qwen3-VL-Reranker-2B sulle top-30 candidate ha fornito i seguenti risultati:
- **Recall@5 complessivo:** 0.600
- **Recall@5 Italiano:** 0.400

Crollo netto rispetto al baseline multi-vettore ColPali (0.875). Il modello fatica enormemente sulle query in italiano per manuali tecnici.

## 2. Mitigation Plan (Fase 4)
Per superare la perdita di interazione semantica fine sul vocabolario IT, e' stato eseguito il seguente piano a costo crescente (senza re-ingestion):

### 2.1 Diagnostica IT->EN
Traduzione manuale delle 10 query IT in inglese tecnico per misurare il "cap" teorico del modello se fosse bypassato il gap linguistico.
- **Risultato (IT tradotte):** Recall@5 = 0.700 (miglioramento netto da 0.400, ma ancora sotto il target 0.80).

### 2.2 Incremento Pool Candidati
In `visual_search` e' stato aumentato il pool dei candidati passati al reranker:
- `eff_tile_k`: 100 -> 300
- `eff_page_k`: 30 -> 60
Costo di latenza proporzionale al raddoppio del numero di immagini passate al VLM da 2B pesi.

### 2.3 Espansione Query IT->EN Deterministica
Sfruttando l'infrastruttura del dizionario a DB (debito §2), i termini tecnici (es. "esploso", "albero motore") vengono tradotti deterministicamente ("exploded view", "crankshaft"). Entrambe le varianti (query originale e pseudo-query EN composta dai sinonimi) vengono "spruzzate" sul database vettoriale e i risultati vengono fusi tramite *max score* per pagina.

### 2.4 Rimisurazione Finale (Pool 60 + Espansione IT->EN)

| Metrica  | Prima del Reranker | Dopo il Reranker (Pool 60 + Espansione) |
|----------|--------------------|-----------------------------------------|
| Recall@1 | 0.333              | 0.400                                   |
| Recall@3 | 0.600              | 0.667                                   |
| Recall@5 | 0.600              | **0.733**                               |
| MRR      | 0.459              | 0.547                                   |

#### Dettaglio per Lingua (Dopo Reranker)
- **EN/DE/ES** (n=5): Recall@5 = 1.000
- **IT** (n=10): Recall@5 = **0.600**

## Conclusioni
**SOGLIA NON RAGGIUNTA: Recall@5 (0.733) < 0.85 e IT (0.600) < 0.80.**
Le tre strategie combinate hanno aumentato le performance (da 0.600 a 0.733 complessivo), e il recall IT e' salito (da 0.400 a 0.600). Tuttavia restiamo strutturalmente sotto il requisito di baseline. Il paradigma single-vector attuale su documentazione tecnica complessa si conferma incapace di catturare il fine-grained alignment visivo che il multi-vettore (ColPali) garantiva nativamente senza alcun reranking.

**Raccomandazione tecnica:** 
Si attende decisione del reviewer per la mossa successiva:
- Eseguire Re-Ingestion usando MRL a massima risoluzione (2048-d) per vedere se lo spazio latente si espande a sufficienza.
- Abbandonare l'approccio single-vector e ripristinare ColPali.
