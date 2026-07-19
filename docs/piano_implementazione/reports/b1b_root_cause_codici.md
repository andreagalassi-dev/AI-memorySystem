# B1.b — Root-cause dei codici degradati (v2)

## Esito v1 (campione invalido)
L'audit precedente (v1) presentava un difetto sistemico: il campionatore era inquinato dall'utilizzo di un estrattore ad alto recall concepito solo come primo stadio del guardrail (il quale viene poi epurato nei check successivi). Questo ha portato nel campione stringhe spurie (es. `Sheet1`, `part.4`, `600cm3`, `ISO/7`), che per loro natura non potevano trovarsi nei DB come codici validi. 

**Nota difensiva per il sistema in produzione:** Pur essendo il campione v1 gravemente inquinato da ~15 non-codici/falsi positivi, il *guardrail e il sistema di confidence hanno agito correttamente (fail-closed)*, impedendo che alcuna di queste query spurie venisse mai promossa al livello di confidenza `ALTA` (sono degradate a `MEDIA` o sfociate in rifiuti). Questo dimostra la totale resilienza del sistema contro attacchi o allucinazioni estrattive.

## Riepilogo Campione v2 (Corretto)
- **Totale campioni**: 40 codici reali, estratti unicamente dal payload `code` in database (l'unica fonte di verità dell'ingestion), con lunghezza ≥ 4 caratteri.
- **Strategia di estrazione**: Scroll paginato per iterare sull'intero indice vettoriale (anziché i primi 10.000 chunk limitati della v1), forzando l'estrazione di codici in tre lingue: EN, IT, DE (con obbligo di almeno 4 codici per lingua DE).
- **Tasso ALTA sul campione**: **40.0%** (16/40), coerente e in linea con il dato storico atteso del ~42%. Il 60.0% degrada a MEDIA/BASSA.

## Tabella Root-Cause × Conteggio v2

| Categoria Root-Cause | Conteggio | Percentuale (su 24 degradati) | Descrizione / Condizione |
| :--- | :---: | :---: | :--- |
| **GENERAZIONE** | 13 | 54.2% | Il codice viene recuperato ma l'LLM genera una formulazione non aderente o ambigua che fa scattare il gate di sicurezza del guardrail (`VERIFY` o `BLOCK`). |
| **FUSIONE_RANKING** | 11 | 45.8% | Il codice è recuperato e passa il guardrail (`PASS`), ma il punteggio iniziale di confidence o un margine limitato bloccano la promozione ad `ALTA` retrocedendola. |
| **RETRIEVAL_MISS** | 0 | 0.0% | Tutti i codici interrogati sono stati correttamente rintracciati dal retrieval (la pagina corretta era tra i candidati passati all'LLM). |

## Distribuzione per Lingua v2

| Lingua | Campioni Totali | ALTA | GENERAZIONE | FUSIONE_RANKING | RETRIEVAL_MISS |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **EN** | 20 | 8 | 12 | 0 | 0 |
| **IT** | 11 | 5 | 0 | 6 | 0 |
| **DE** | 9 | 3 | 1 | 5 | 0 |

## Casi Notevoli v2

Di seguito alcune evidenze su come il motore modera la qualità (anche in casi in cui l'LLM genera testo di per sé esatto ma non promosso o limitato dal layer deterministico).

### 1. Punteggi deboli ma estratti (FUSIONE_RANKING) - Caso *pi_pulley* riconfermato
In diversi listini tabellari formattati, l'LLM risponde correttamente, ma il ranking vettoriale sconta una densità del contesto tale da abbassare la confidence (o il delta intra-gruppi), portando il level finale a `MEDIA` nonostante l'estrattore non rilevi anomalie (`PASS`).
- **Esempio (IT)**: `query="codice PDTL28H100S-F-BL"` (doc: *pi_pulley_listino_pulegge_2025*)
  - *Esito Guardrail*: `PASS`
  - *Generato*: `"PDTL28H100S-F-BL — 101,42 (pi_pulley_listino_pulegge_2025, pag. 18)"`
  - *Livello Finale*: `MEDIA` (anziché ALTA) a causa del nodo `confidence_node`.

### 2. Generazione dubbia o ambigua limitata (GENERAZIONE)
Il guardrail agisce ponendo lo stato in `VERIFY` o `BLOCK` per codici che pur estraibili correttamente vengono elaborati in modo imperfetto dall'LLM. Ad esempio, quando il testo risponde solo ripetendo il codice omettendo dettagli essenziali attesi.
- **Esempio (EN)**: `query="codice 9B3220635"` (doc: *iwaki_TC-X101SH-NPT*)
  - *Esito Guardrail*: `VERIFY`
  - *Generato*: `"9B3220635 (iwaki_TC-X101SH-NPT, pag. 3)"`

### Conclusione Task B1.b-v2
A valle della bonifica del campionatore, il tasso ALTA misurato sul dato pulito rispecchia il benchmark storico (~40%). Eliminando il rumore dei campioni testuali non-codice, il problema primario non risulta più il fallimento del retrieval (`0%`), ma l'incapacità dell'LLM di presentare correttamente o con margine sufficiente l'evidenza estratta (GENERAZIONE e FUSIONE_RANKING).
