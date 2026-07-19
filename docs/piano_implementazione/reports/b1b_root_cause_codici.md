# B1.b — Root-cause dei codici degradati

## Riepilogo Campione
- **Totale campioni**: 40 codici estratti dai payload dei chunk indicizzati.
- **Strategia di estrazione**: Utilizzo di `extract_candidates` (stessa logica del guardrail `src/retrieval/guardrail.py`), limitato a un massimo di 5 codici per documento, su un set diversificato di documenti per garantire almeno 8 brand e lingue multiple.
- **Tasso ALTA sul campione**: **32.5%** (13/40) contro un dato storico stimato al 42%. Il 67.5% (27/40) degrada a MEDIA o BASSA.

## Tabella Root-Cause × Conteggio
La classificazione delle cause di fallimento/degrado (per i 27 casi NON-ALTA) evidenzia come il problema predominante sia legato alla **Fusione e Ranking**, seguito dal fallimento del Retrieval.

| Categoria Root-Cause | Conteggio | Percentuale (sul tot. errori) | Descrizione / Condizione |
| :--- | :---: | :---: | :--- |
| **FUSIONE_RANKING** | 15 | 55.6% | Il codice è presente nei candidati, il guardrail non lo blocca (PASS), ma il livello di confidenza non permette la promozione ad ALTA (es. basso score o margine minimo che scatena il refusal). |
| **RETRIEVAL_MISS** | 8 | 29.6% | Il codice non viene recuperato tra i candidati del database vettoriale, risultando assente nel set di evidenze fornite all'LLM. |
| **GENERAZIONE** | 4 | 14.8% | L'evidenza è corretta e presente nei candidati, ma l'LLM genera una formulazione errata o allucinata (ad es. associa un codice di un componente alla quantità), scatenando un blocco (BLOCK o VERIFY) da parte del guardrail. |
| **ESTRAZIONE_CHUNKING** | 0 | 0.0% | Non si sono riscontrati casi in cui il codice non era nel chunk (essendo i campioni estratti proprio dai chunk indicizzati). |
| **ALTRO_DA_INDAGARE** | 0 | 0.0% | Nessun caso residuo. |

## Distribuzione per Lingua

| Lingua | Campioni Totali | ALTA | FUSIONE_RANKING | RETRIEVAL_MISS | GENERAZIONE |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **IT** | 20 | 4 | 10 | 2 | 4 |
| **EN** | 19 | 9 | 5 | 5 | 0 |
| **und** (indefinita) | 1 | 0 | 0 | 1 | 0 |

## Casi Notevoli

Di seguito, un'analisi approfondita su alcuni comportamenti sistemici e peculiarità emerse dall'audit (con raw generato in accompagnamento):

### 1. Refusal per Bassa Confidenza (FUSIONE_RANKING)
Molti codici, pur essendo trovati nel retrieval, restituiscono come testo generato: *"Non ho evidenza sufficiente per rispondere."*
Questo accade quando il livello di *confidence* (score/margine) è troppo basso e innesca un rifiuto (`refuse_node`). 
- **Esempio (EN)**: `query="codice 600cm3"`
  - *Esito Guardrail*: `PASS`
  - *Generato*: `"Non ho evidenza sufficiente per rispondere."`

### 2. Guardrail Block per Errori di Associazione (GENERAZIONE)
Il guardrail dimostra la sua efficacia (fail-closed) quando l'LLM allucina associazioni tra i codici e le quantità o altre entità della tabella, alterando o unendo codici diversi.
- **Esempio (IT)**: `query="codice 6316C3"` (Documento: smem_motori_elettrici_catalogo_2025)
  - *Esito Guardrail*: `BLOCK`
  - *Generato*: `"6316C3 — 3152P(vertical) (qty 7316) (smem_motori_elettrici_catalogo_2025, pag. 20)"`
  - *Nota*: L'LLM associa in modo anomalo un altro numero ("7316") scatenando il blocco. 
- **Esempio (IT)**: `query="codice 208/240VAC"` (Documento: DirectKitchen_Forni_Schemi_IT_EN)
  - *Esito Guardrail*: `BLOCK`
  - *Generato*: `"La cella di lievitazione viene consegnata con voltaggio di 208/240VAC 1ph 60 Hz (DirectKitchen_Forni_Schemi_IT_EN, pag. 6). Le resistenze per S50/100/105/120/125L sono 208/240VAC 1ph (DirectKitchen_Forni_Schemi_IT_EN, pag. 96)."`
  - *Nota*: La risposta cita svariati altri codici ("S50/100/105/120/125L") che verosimilmente non passano il check del guardrail (assenti dal catalogo come codici letterali validi recuperati), imponendo il blocco dell'output.

### 3. Punteggi deboli ma estratti (FUSIONE_RANKING con ALTA probabile degradata)
In alcuni listini, l'LLM risponde correttamente ma il sistema chiude comunque in final_level `MEDIA` (probabilmente retrocessa da bassa confidenza intra-gruppo prima che passasse per il guardrail o innescando check diagnostico su fusion).
- **Esempio (IT)**: `query="codice PD33H150S-F-BL"`
  - *Esito Guardrail*: `PASS`
  - *Generato*: `"PD33H150S-F-BL — 220,56 (pi_pulley_listino_pulegge_2025, pag. 19)"`

## Conclusione del Task B1.b
- L'infrastruttura deterministica di audit è implementata e supportata da TDD (tests `test_audit_degraded_codes.py`).
- Il tasso `ALTA` base misurato sui modelli caldi per questo campione è 32.5%.
- La maggior parte del degrado qualitativo (`MEDIA` / `BASSA`) deriva da score bassi nelle evidenze di retrieval (ranking debole o margine minimo di agreement intra-gruppi) che portano o a Refusal esplicito, oppure alla mancata promozione ad `ALTA`.
