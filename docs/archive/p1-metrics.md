# P1 — Metriche del gate di ingestion (definizione operativa)

> Scope: **spike di misurazione** (SPEC P1). Qui si definiscono i **due numeri**
> del gate e come si calcolano **automaticamente** dagli output dei parser
> (baseline PyMuPDF4LLM, Marker, eventuale Docling) **dato un ground truth
> annotato a mano**. Coerente con SPEC §F.1: metriche **disaggregate per lingua**,
> mai in media unica (una lingua debole non deve nascondersi in una media).
> NON è la pipeline finale.

## Lingue in scope
`IT`, `EN`, `DE` (core del progetto) + `FR`, `ES` (presenti nel corpus reale:
Silca multilingua, foglio spagnolo Electrolux). `ZH` (cinese in DeliTools) e altre
sono **registrate ma fuori dai numeri di gate** (non sono lingue target).

---

## Metrica 1 — Table Cell Accuracy (per lingua)

**Definizione.** Per una lingua `L`, sull'insieme delle tabelle candidate assegnate
a `L` (incluse le colonne lingua-specifiche delle tabelle multilingua), la frazione
di **celle di dato** in cui il valore estratto dal parser coincide con il ground
truth, dopo allineamento riga/colonna e normalizzazione.

**Allineamento (automatico, key-based).**
1. La colonna **`codice`** è la chiave di riga. Le righe del parser si allineano a
   quelle del GT per `codice` normalizzato.
2. Per ogni riga del GT **trovata** nel parser: ogni cella delle colonne di dato
   non-chiave (`descrizione`, `quantita`, `prezzo`, `pos`, …) è **corretta** se il
   valore normalizzato coincide con il GT.
3. Righe del GT **non trovate** (codice mancante) → tutte le loro celle contano
   come **errate** (cattura le righe perse / celle unite che fondono più righe).
4. Righe in più prodotte dal parser (codice non nel GT) → non abbassano l'accuracy
   ma si riportano a parte come **false-positive rows** (segnale di rumore/allucinazione struttura).

**Formula.**
```
Table Cell Accuracy(L) = celle_dato_corrette(L) / celle_dato_totali_GT(L)
```
Riportati insieme, per lingua:
- `cell_accuracy` (metrica 1),
- `row_recall` = righe GT trovate / righe GT totali (quante righe si perdono),
- `false_positive_rows` (contesto, non entra nella formula).

**Normalizzazione (deterministica, applicata a parser E GT prima del confronto).**
- rimozione markup Markdown: `*`, `_`, `` ` `` (⚠️ risolve `V0**91568**00007` → `V09156800007`);
- `<br>` → separatore; per la **chiave `codice`** si rimuovono anche gli spazi interni;
- collapse degli spazi multipli, trim;
- numerici (`quantita`, `prezzo`): virgola/punto decimale unificati, confronto numerico;
- `codice`: **case-sensitive** (i codici lo sono); `descrizione`: confronto
  normalizzato **case-insensitive**. Come numero **secondario/diagnostico** si
  riporta anche una versione *fuzzy* della descrizione (similarità token ≥ 0.90),
  ma il numero di gate è il match esatto normalizzato.

## Metrica 2 — Structure Fidelity Score (0 / 1 / 2, per tabella)

Punteggio per **tabella** (poi aggregato per lingua come media + distribuzione),
calcolabile automaticamente confrontando la struttura estratta col GT:

| Score | Significato | Regola automatica |
|-------|-------------|-------------------|
| **2** | struttura completa e leggibile | `row_recall ≥ 0.90` **e** n. colonne estratte = n. colonne GT **e** frazione celle multi-valore `<br>` < 5% **e** nessun header spezzato |
| **1** | struttura parziale | tabella riconoscibile ma manca una condizione del 2 (colonne/righe perse, header spezzato, molte celle multi-valore) |
| **0** | irriconoscibile | nessuna tabella prodotta **oppure** `row_recall < 0.25` |

**Header spezzato** (rilevatore automatico): una parola dell'header attesa risulta
divisa su due colonne adiacenti (es. `RICAMBI/SPAR` | `E PARTS`, `2551-P0485-1<br>A`|`rtikelnr.`).

## Metrica 1-bis — Breakdown PER COLONNA (aggiunto in P1.4-light)

L'accuratezza aggregata mescola colonne di natura diversa. Per il go/no-go si usa
il **breakdown per colonna**, per lingua:
- **codeAcc** = frazione di codici GT ritrovati correttamente nell'output (≡ row_recall,
  il codice è la chiave). **È la metrica decisiva** per un catalogo ricambi.
- **qtyAcc / priceAcc** = match numerico esatto (normalizzato) di `quantita` / `prezzo`,
  fra le righe recallate. NB: se il codice non è recallato, la riga non è identificabile
  → qty/price di quella riga contano come errate (accoppiamento voluto con codeAcc).
- **descAcc** = match della descrizione con soglia **fuzzy** (Jaccard token-set ≥ 0.6),
  perché case/spaziatura/ordine minori non sono errori sostanziali; si riporta anche
  `descAcc_strict` come riferimento severo.
- **structFid** = Structure Fidelity 0/1/2 (Metrica 2).

> **Nota GT descrizioni (P1.4-light):** le descrizioni GT di `MAMOLI_P231` erano state
> compilate dal Markdown baseline **mangled** (`aCCeSSori FiSSaGGio…`). Sono state
> **rigenerate dal text-layer nativo pulito** (`page.get_text`, ancorate al codice —
> `scripts/regenerate_gt_descriptions.py`), backup in `expected.pre_descfix.csv`. Le
> altre tabelle avevano descrizioni GT già pulite. Questo rende `descAcc` non più
> viziato a favore della baseline.

## Aggregazione per lingua (SPEC §F.1)
Output finale del gate = **una riga per lingua**:
```
lingua | n_tabelle | cell_accuracy | row_recall | structure_fidelity_media | fp_rows
IT     | …          | …             | …          | …                        | …
EN     | …          | …             | …          | …                        | …
DE     | …          | …             | …          | …                        | …
FR/ES  | …          | …             | …          | …                        | …
```
**Mai** un numero unico aggregato. Il go/no-go (P1.6) guarda la lingua **peggiore**.

---

## Schema del ground truth
Un GT per tabella candidata, in `data/ground_truth/<table_id>/`:
- **`expected.csv`** — la tabella corretta come griglia righe×colonne canoniche.
  Colonne canoniche minime: `pos, codice, descrizione, quantita, prezzo` (le non
  applicabili restano vuote). Per le tabelle **multilingua** (Silca) la descrizione
  è per-lingua: colonne `descrizione_it, descrizione_en, descrizione_de,
  descrizione_fr, descrizione_es` con `pos`/`codice` neutri.
- **`meta.json`** — `table_id`, file sorgente, `page`/`sheet`, `lang` (o lista
  lingue), colonne canoniche usate, colonne lingua-specifiche, note.
- (solo PDF) **`page.png`** — rendering della pagina per la validazione visiva umana.
- **`baseline.md` / `baseline.csv`** — estrazione baseline, come bozza da correggere.

`expected.csv` viene **pre-compilato** dallo script (`prepare_ground_truth.py`) con
la bozza baseline; l'umano **corregge sull'originale** (PNG) e conferma. Il GT è la
verità: si annota una volta.

## Contratto dello script di valutazione (P1.6 — non in P1.2)
`scripts/evaluate_ingestion_p1.py`:
- input: `data/ground_truth/**` + output parser in `output/spike/<engine>/**`;
- applica normalizzazione + allineamento key-based sopra definiti;
- emette per (engine × lingua) i numeri, in `output/spike/<engine>/_metrics.json`
  e una tabella riassuntiva per il documento di decisione `docs/p1-gate-decision.md`.
