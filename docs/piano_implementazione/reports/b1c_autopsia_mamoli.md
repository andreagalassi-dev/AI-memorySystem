# Autopsia Mamoli (Task B1.c)

## 1. Identificazione dell'Estrattore
Il processo responsabile dell'estrazione per il catalogo *Mamoli_Catalogo_Ricambi_2021_IT_EN* è il **TextTableExtractor** (situato in `src/ingestion/extraction/text_table.py`). 
Questo perché il file è un PDF *born-digital* (con text-layer affidabile). La funzione `route_table_page` (in `src/ingestion/run_massive_extraction.py`) valuta `reliable_text_layer(page)` e instrada le pagine con testo nativo all'estrattore deterministico block-aware (`TextTableExtractor`), per evitare che il modello VLM corrompa i codici.
Le righe valide vengono scritte nel database dalla funzione `_persist_tx` in `src/ingestion/run_massive_extraction.py` (`estrazione.righe_listino`).

## 2. Copertura
L'audit eseguito tramite lo script `scripts/audit_mamoli_extraction.py` mostra:
- Pagine totali PDF: 237
- Pagine presenti in `righe_listino`: 67 (pagine 8-185, con molti buchi all'interno).
- Pagine attese mancanti: 145.
- Buchi rilevati: 156, 186-237, ecc.
L'output di copertura è stato salvato in `output/audit/b1c_mamoli_coverage.json`.

## 3. Confronto Verbatim (Estrazione Locale)
L'estrattore `TextTableExtractor` è stato rieseguito localmente senza scrivere sul DB. Il risultato (`output/audit/b1c_mamoli_local_extraction.json`) dimostra che le pagine mancanti sono estraibili perfettamente:

- **Campione Pagine Coperte:**
  - **P. 154:** 8 righe estratte (es. `90440C` "Tubo di alimentazione flessibile..." a 36.50). Coerente col PDF.
  - **P. 155:** 8 righe estratte.
  - **P. 185:** 7 righe estratte (es. `90402C` a 23.50).

- **Campione Pagine nei Buchi:**
  - **P. 156:** 3 righe estratte localmente.
  - **P. 200:** 0 righe estratte (effettivamente pagina di transizione/sommario anomalo).
  - **P. 230:** **188 righe estratte!** (es. `V09008000001` "Rosone/Wall plate 2817 Stylos - Cromo/Chrome" a 90.00). Coerente con la tabella borderless visibile nel PDF.

Il confronto verbatim certifica che l'estrattore in sé funziona anche per le pagine mancanti. Il problema è quindi *a monte* dell'estrattore.

## 4. Causa dell'Interruzione (Catena Causale)
L'estrazione non muore per timeout, loop, o errori silenziati durante il grounding (le 188 righe a p230 superano intonse la funzione `partition()`). Il problema è nel **Routing di Fast-Dispatch**.

1. In `src/ingestion/run_massive_extraction.py`, il runner inizia classificando le pagine del PDF tramite `dispatch_pdf`.
2. Se `pd.route is Route.FAST_TEXT`, il codice esegue un `continue`, saltando il `table_detect` e il binario AI/`TextTableExtractor`.
3. In `src/ingestion/page_classifier.py`, una pagina diventa `FAST_TEXT` se `chars >= 300` e NON è classificata come `heavy`. La classificazione `heavy` scatta se c'è un forte segnale grafico (`n_draw_items >= 4000` o `n_drawings >= 1000`).
4. **Le prove:** 
   - La pagina 154 ha 14003 `draw_items` (griglia rigata ben delineata) e viene marcata come `HEAVY`. Quindi passa all'estrattore.
   - La pagina 230 ha 10887 `chars` ma solo **194 `draw_items`** (è una tabella *borderless*, priva di linee divisorie).
   - Mancando le linee vettoriali, la pagina 230 fallisce i check `graphic_strong` e `heavy`, venendo etichettata dal `page_classifier` come `PageClass("testuale", dual=False)`.
   - `route_for` in `fast_dispatch.py` la mappa a `Route.FAST_TEXT`.

**Conclusione:**
La "morte" dell'estrazione dopo p185 e la presenza di buchi (come p156) non sono causate dall'estrattore o dal DB. È una limitazione sistematica dell'euristica di classificazione (`page_classifier.py` / `fast_dispatch.py`). Le tabelle "borderless" (con poche righe vettoriali) vengono scambiate per pagine di solo testo (`FAST_TEXT`) e scartate dal runner prima ancora di verificare con `is_table_page` se contengono dati tabellari.

## 5. Verifica e quantificazione del reviewer (19/07/2026)

Catena causale **CONFERMATA** con la correlazione completa route×DB su tutte le 237 pagine
(dispatch rieseguito dal reviewer):

- le **67 pagine nel DB sono tutte `heavy`** (0 contro-esempi);
- delle 145 mancanti: **99 `fast_text`** (spiegate dalla teoria) e 46 `heavy`;
- le 46 `heavy` mancanti NON sono un secondo modo di fallimento: l'estrattore rieseguito
  su tutte produce **0 righe** (sono esplosi/tavole senza listino — l'euristica
  `expected_pages` di questo audit era troppo inclusiva; il totale "attese 203" va letto
  con questo caveat).

**Quantificazione del danno (estrattore rieseguito su tutte le 145 pagine mancanti,
sola lettura):** 75 pagine hanno righe estraibili, TUTTE `fast_text`, per un totale di
**1.698 righe recuperabili contro le 280 oggi in DB** (~6×). Concentrazione in coda:
p224-231 valgono ~1.300 righe (i listini borderless — incluse le p230-231 già note da T2).

Correzione al registro storico: il PDF Mamoli ha **237 pagine**, non 285 (verificato
PyMuPDF); "p185/285" nei documenti precedenti era errato.

Fix del routing (es. `is_table_page` PRIMA del branch fast_text, o segnale tabellare
text-based nel classifier) = **epica C**, con questi numeri come baseline del gate.
