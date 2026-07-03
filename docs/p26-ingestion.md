# P2.6 — Normalizzatore multi-brand a configurazione + ingestion massiva

> Data: 2026-07-03 · Corpus: 54 PDF / 3247 pagine / 11 categorie ·
> Indice finale: **32.775 chunk** (234 spike GT-validati + 32.541 corpus).
> Tutti i numeri qui sotto sono osservati da esecuzioni reali (log nei commit).

## 1. Registro brand a configurazione (`config/brands.json`)

- 3 profili espliciti (mamoli con rejoin GT-validato, electrolux `0US***`,
  delitools numerico-9) + **profilo `default` passthrough**: solo cleanup
  sicuro + rilevamento conservativo del token-codice, nessuna regex inventata
  per i 50 brand senza GT. Tipo `default_passthrough` sempre loggato.
- Registro **unificato col router** (famiglie Tier A dallo stesso file):
  chiude il debito della gate decision P1 §5.
- Regressioni osservate: output normalizzatore Mamoli **byte-identici**,
  contratti GT CSV 1:1, 121/121 test, ruff pulito.

## 2. Estrazione massiva (Docling do_ocr=False, TableFormer ACCURATE)

- Pilota misurato: 2.25 s/pag → run completa: **2556/2556 pagine, 0 fallimenti,
  124 min**, checkpoint per-pagina (md + progress flush: un OOM-kill perde al
  massimo una pagina).

## 3. Chunking generalizzato

- **23.354 chunk-riga con codice + 9.187 chunk-testo** da 54 documenti;
  10 reject (`fused_multi_code_cell`), **0 violazioni della guardia
  `code_not_in_source`** (nessun codice fabbricato dall'estrazione).
- Lingue rilevate (stopword, per documento): EN 32, IT 14, **DE 3**, FR 1,
  und 4 — il buco DE dichiarato in P2.4 è chiuso a livello di corpus.
- Righe pos/qty disambiguate per posizione (numerici a sinistra del codice =
  pos, a destra della descrizione = qty).

## 4. Le 196 pagine mute — esito esperimento e decisione

- **Primo esperimento invalido e scoperto tale**: `do_ocr=True` senza EasyOCR
  installato non OCRa nulla in silenzio (output `<!-- image -->`). Il test
  valido richiede dep `easyocr` + **`force_full_page_ocr=True`**.
- Stratificazione corretta in corsa: le vere tabelle-mute del corpus sono
  quasi solo la famiglia vector-text `kbs_gastrotechnik_4055041`; il resto
  sono tavole/esplosi (l'OCR vi estrae solo i titoli).
- **Criterio ≥3/4 sulle vere tabelle-mute: 3/3** (tabelle
  `Pos|Sachnr|Menge|Bezeichnung` complete e leggibili).
- **Decisione**: corsia OCR per le 3 pagine-tabella (chunk marcati
  `ocr_source` + `degraded`: la guardia è circolare su testo OCR → il
  guardrail P3 li degrada a "verifica manuale"); le altre ~193 pagine mute
  (esplosi/tavole) → P2.7 ColPali. Nessun testo OCR indicizzato in silenzio.

## 5. Indicizzazione e regressione retrieval (l'allarme è scattato, analizzato)

Ingestion: 7/7 check verdi a 32.775 punti (1514 senza vettore denso = chunk
senza descrizione, solo lessicali).

**Eval P2.4 (54 query) sull'indice 140× più grande — strategia routed:**

| | R@1 | R@3 | R@10 | MRR |
|---|---:|---:|---:|---:|
| P2.4 (234 chunk) | 0.944 | 1.000 | 1.000 | 0.972 |
| P2.6 prima del fix | 0.611 | — | — | 0.691 |
| **P2.6 finale** | **0.704** | 0.815 | **0.870** | 0.778 |

Per tipo (finale): **code 0.938/R@3=1.0**, **mixed 0.875/R@3=1.0**,
natural 0.533. Per lingua: EN 0.714, ES 0.833, IT 0.676.

**Analisi dell'allarme (soglia R@1<0.85), due cause trovate:**

1. **Bug reale, fixato e misurato**: le parole-marker ("part number",
   "codice") nelle query matchavano migliaia di chunk-testo sommergendo il
   token-codice unico (19.9 vs 15.9 BM25). Fix: il canale lessicale della
   strategia routed cerca **solo il `code_token`** estratto dal router
   (mixed: sparse sul token + dense sulla frase). Effetto: code 0.75→0.938,
   mixed 0.50→0.875, entrambi R@3=1.0.
2. **Eval set v0 sotto-specificato per il corpus multi-brand** (non un bug
   del retrieval): ≥3 dei 5 miss naturali residui sono **risposte corrette
   non accreditate** — es. "aeratore 24x1 di ricambio" → top-1 codice `0448`
   "aeratore 24x1/24x1 aerator" (pagina Mamoli fuori dal GT v0); "pair of
   handles" → `20001N` "coppia maniglie/pair of handles". L'expected v0
   conosceva solo il corpus a 2 documenti. **L'eval non viene ritoccato per
   gonfiare i numeri**: la revisione (query brand-aware + expected allargati
   + più coppie per lingua) è assegnata a P2.8, che esiste esattamente per
   il recall su scala globale.

## 6. Debiti e rinvii dichiarati

- Qualità fine delle tabelle dei brand senza GT: misurata indirettamente
  (guardia + spot-check); GT aggiuntivi eventualmente a P2.8.
- Eval v1 multi-brand + validazione tecnico: P2.8.
- Pagine grafiche (878) e mute non-tabellari: P2.7 (ColPali).
- Rumore residuo campi qty/pos su alcune tabelle atipiche (es. indici di
  sezione): accettato, i campi sono best-effort e il codice resta esatto.
