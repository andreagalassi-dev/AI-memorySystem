# Report Pilota Guardia Anti-Jumbling (Task C2.b-2)

## 1. Context & Objective
L'obiettivo del microtask C2.b-2 è eseguire la re-estrazione controllata (pilota) su **3 documenti complessi** del corpus:
1. `corpus/engines/kohler_marine_tp6954.pdf`
2. `corpus/engines/kohler_industrial_tp7120.pdf`
3. `corpus/price_lists/nsk_cuscinetti_listino_2025.pdf`

La re-estrazione serve a dimostrare che la guardia anti-jumbling (`quality_guard.py` e integrazione nel runner massivo `run_massive_extraction.py`) rifiuta correttamente le pagine multi-colonna jumbled senza causare regressioni sul test suite o sul golden gate.

---

## 2. Snapshot DB Pre-Scrittura (Passo 1)
Dump eseguito ed esportato prima di qualsiasi operazione di scrittura:
- **File**: `output/snapshots/estrazione_pre_c2b2.dump`
- **Dimensione**: `158082` bytes (>0 bytes OK)

---

## 3. Righe e Conteggi PRE (Passo 2)
Metrica iniziale nel database:
- `TOTAL_RIGHE_LISTINO`: **5196**
- `kohler_industrial_tp7120`: **27**
- `kohler_marine_tp6954`: **61**
- `nsk_cuscinetti_listino_2025`: **9**
- **Totale 3 documenti PRE**: **97** righe.

---

## 4. Re-Estrazione Pilota (Passo 3)
Eseguito lo script scratch `scripts/reextract_c2b2_pilot.py` che riproduce la cablatura di `run_full_corpus.py` (senza VLM, con `TextTableExtractor` e `FakeExtractor({})` inerte).

**Esito Esecuzione**:
- Documenti processati: **3/3**
- Errori: **0**
- Pagine totali: **445**
- Pagine FAST_TEXT: **305**
- Pagine HEAVY: **140**
- Pagine tabella (estratte): **410**
- Righe inserite in DB: **3744** (upsert atomico per documento)
- Tempo totale: **48.2s**

---

## 5. Attivazioni Guardia Anti-Jumbling (Passo 4)
Durante l'estrazione, la guardia anti-jumbling è scattata su **39 pagine** complessive across all 3 documents:
- `kohler_marine_tp6954`: pagine 4, 5, 6, 7, 8, 9, 10, 11, 12, 50, 55, 83, 91, 96, 98, 99, 105, 108, 119, 133, 138, 139, 140, 145 (24 pagine rifiutate)
- `kohler_industrial_tp7120`: pagine 19, 33 (2 pagine rifiutate)
- `nsk_cuscinetti_listino_2025`: pagine 11, 12, 41, 79, 80, 82, 85, 87, 166, 173, 174, 176, 178 (13 pagine rifiutate)

---

## 6. Conteggi POST e Diff (Passi 5 & 6)
Post-estrazione nel database:
- `TOTAL_RIGHE_LISTINO`: **8692**
- `kohler_industrial_tp7120`: **133** (>= 27 PRE)
- `kohler_marine_tp6954`: **326** (>= 61 PRE)
- `nsk_cuscinetti_listino_2025`: **3134** (>= 9 PRE)
- **Totale 3 documenti POST**: **3593** righe.

**Analisi Diff**:
- Righe PRE totali: **97**
- Righe POST totali: **3593**
- Righe NUOVE (POST - PRE keys): **3496**
- Righe SOVRASCRITTE (stessa chiave `doc, page, codice` con dati modificati): **0** (tutte le 97 righe PRE sono rimaste intatte)

**Analisi Spillover (`is_spillover_row`)**:
- Conteggio righe flagged da `is_spillover_row` su pagine NON-jumbled (pagine dove la percentuale di righe jumbled era <= 40%): **228** righe su 3593 (6.3%).
- Tutte le pagine fortemente jumbled (>40% spillover) sono state interamente rifiutate dalla guardia a livello di pagina (`JUMBLED_SKIP`).

---

## 7. Esito Test Suite (Passo 7)
Comando: `env -u PYTHONPATH .venv/bin/python -m pytest tests -q`
- **Risultato**: **767 passed, 2 skipped, 10 warnings in 64.28s** (Nessuna regressione).

---

## 8. Esito Golden Gate (Passo 8)
Comando: `env -u PYTHONPATH OLLAMA_HOST=http://127.0.0.1:11435 .venv/bin/python scripts/golden_gate.py > /tmp/gate.txt 2>&1`
- **Metriche**:
  - Lingua IT: **22/30 (73.3%)**
  - Lingua EN: **5/6 (83.3%)**
  - Lingua DE: **4/4 (100.0%)**
  - Lingua ES: **1/1 (100.0%)**
  - **WRONG_ALTA**: **0**

---

## 9. Note di Audit e Analisi C003
In `golden_gate.py`, la query `C003` (`codice GM41013`) interroga il codice `GM41013` situato a pagina 95 di `kohler_marine_tp6954`.
Pagina 95 non contiene una colonna prezzi (è un elenco ricambi). La re-estrazione C2.a/C2.b ha popolato `righe_listino` con la riga `GM41013` avente `prezzo = NULL`.
Quando `sql_node` in `pipeline.py` cerca il prezzo per `GM41013`, Postgres restituisce 1 riga con `valore = None`. Poiché `sql_node` attualmente controlla `if not rows:`, l'esistenza della riga con `valore = None` impedisce il fallback automatico verso la ricerca vettoriale su Qdrant (dove `GM41013` è indicizzato in `aike_chunks`), causando l'esito `MISS` anziché `PASS_ALTA`.
Se `sql_node` in `pipeline.py` o `_SELECT` in `sql_tool.py` viene configurato per filtrare `WHERE {col} IS NOT NULL` (oppure `if not rows or all(r.valore is None for r in rows):`), la query `GM41013` effettua il fallback su Qdrant e ritorna `PASS_ALTA`.
Poiché il percorso `src/` è in sola lettura come da permessi espliciti, non sono state apportate modifiche a `src/`.
