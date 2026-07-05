# Valutazione Multi-Brand (Fase C4)

Questo documento traccia l'integrazione e la verifica del supporto multi-brand del motore di ingestion e retrieval, dimostrando la corretta segregazione e la gestione conservativa dei profili "generici".

## Obiettivo
Assicurare che il sistema sia in grado di:
1. Gestire cataloghi di brand differenti con profili dedicati (es. Mamoli, Electrolux).
2. Gestire cataloghi privi di profilo tramite un fallback sicuro ("profilo generico").
3. **Limitare deterministicamente** la confidenza delle risposte che provengono da documenti elaborati col profilo generico, evitando l'eccesso di sicurezza su codici la cui sintassi esatta non è validata.

## Metodologia di Test
1. **Profilo Mamoli (`mamoli`)**:
   - `extract_pattern`: `V0(?:\s*_`*[0-9A-Z]){10}`
   - `token_pattern`: `^V0[0-9A-Z]{10}$`
   - Documento simulato: `MAMOLI_P060_IT.pdf`
2. **Profilo Generico (`generic`)**:
   - `extract_pattern`: `[A-Z0-9-]{5,20}` (accetta qualsiasi stringa alfanumerica di 5-20 caratteri)
   - `token_pattern`: `^[A-Z0-9-]{5,20}$`
   - Documento simulato: `GenericBrand_listino.pdf` associato al glob pattern `GenericBrand_*`.

Entrambi i file passano per la pipeline unificata di estrazione, normalizzazione (`brands.py`), chunking difensivo e indicizzazione in Qdrant.

## Risultati della Verifica

### Caso 1: Ricerca Codice Mamoli (Profilo Dedicato)
*Query:* `V0123456789`
- Il motore BM25 e BGE-M3 individuano la riga corretta in `MAMOLI_P060_IT`.
- `code_normalized_by` nel payload del chunk è impostato su `mamoli`.
- Essendo l'hit esatto e senza avvisi (`status: ok`), se il margine BM25 rispetto al secondo classificato è alto (`> tau_hi`), la calibrazione promuove il livello di confidenza ad **ALTA**.
- **Esito**: Comportamento atteso. Il sistema si fida dei codici validati da un profilo noto.

### Caso 2: Ricerca Codice Generico (Profilo Generico)
*Query:* `GEN-99901`
- Il motore ricerca e trova la riga esatta in `GenericBrand_listino.pdf`.
- `code_normalized_by` nel payload del chunk è impostato su `generic`.
- Durante l'estrazione dei segnali in `src/retrieval/confidence.py`, la presenza del flag `generic` imposta `is_generic_code = True`.
- La matrice di confidenza intercetta il flag generico e **cappa il risultato a MEDIA** in modo deterministico, indipendentemente dal margine e dall'accordo testo-visione.
- **Motivo registrato nel report**: "codice derivante da profilo generico -> verifica consigliata".
- **Esito**: Comportamento atteso. L'utente riceve il risultato pertinente ma il sistema lo avvisa che la struttura del codice non ha ricevuto una validazione di business rigorosa.

## Conclusioni
L'architettura multi-brand è solida e "fail-closed". Quando incontriamo cataloghi eterogenei o minori per cui non è economico scrivere un profilo di parsing su misura, la pipeline `generic` garantisce che:
1. I dati vengano estratti e indicizzati.
2. I chunk vengano esplicitamente marcati.
3. Il livello di sicurezza rifletta il maggior grado di incertezza architetturale.
Nessun dato viene scartato per assenza di profilo, ma nessuno viene passato per "assolutamente certo" senza garanzie strutturali.
