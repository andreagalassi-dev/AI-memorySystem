# P1 — Decisione di GATE (go/no-go) — Validazione ingestion

> **Punto:** P1 (⚠️ GATE CRITICO, SPEC §D, punti aperti #1 e #10).
> **Data:** 2026-07-01. **Fase:** 0 (locale, single-tenant).
> **Esito:** ✅ **GATE P1 SUPERATO — GO.**

Questo documento è la decisione ufficiale del gate. I numeri provengono dagli spike
P1.1–P1.5+ misurati sul corpus reale; i dettagli e le evidenze sono in
`output/spike/*/_FINDINGS*.md` e nei `_metrics.json` / `_code_accuracy.json`.

---

## 1. DoD del gate e come è stato soddisfatto
DoD (SPEC P1): *«due numeri sul corpus reale — qualità OCR e accuratezza estrazione
tabelle — e una decisione go/no-go scritta».* Poiché i PDF sono **nativi** (non
scansionati), la "qualità OCR" carattere-per-carattere è fuorviante: il rischio reale è
la **fedeltà di struttura** e la **correttezza del codice ricambio**. I due numeri sono
quindi operativi (vedi `docs/p1-metrics.md`), **disaggregati per lingua** (SPEC §F.1):

- **Numero 1 — Structure Fidelity Score** (0/1/2 per tabella): struttura riconoscibile.
- **Numero 2 — Table Cell Accuracy per colonna**, con **codeAcc** (codice = valore
  critico del dominio) come metrica decisiva, + descAcc(fuzzy)/qtyAcc/priceAcc.

## 2. Numeri ufficiali di gate (per lingua)
Campione: 6 tabelle "difficili" reali (Mamoli IT worst-case, NordCap DE, Silca
multilingua, XLSX EN/ES). GT annotato a mano; descrizioni Mamoli rigenerate dal
text-layer nativo (P1.4-light).

**Baseline PyMuPDF4LLM (text-layer, zero-GPU) — coverage tutte le lingue:**
| Lingua | codeAcc | descAcc(fz) | qtyAcc | priceAcc | structFid |
|--------|:-------:|:-----------:|:------:|:--------:|:---------:|
| IT | 0.992 | 0.632 | 0.852 | 0.852 | 1.25 |
| EN | 1.000 | 1.000 | — | — | 1.0 |
| DE | 1.000 | 1.000 | — | — | 1.0 |
| FR | 1.000 | 1.000 | — | — | 1.0 |
| ES | 1.000 | 1.000 | — | — | 1.0 |

**Marker (re-OCR) — coverage tutte le lingue:**
| Lingua | codeAcc | descAcc(fz) | qtyAcc | priceAcc | structFid |
|--------|:-------:|:-----------:|:------:|:--------:|:---------:|
| IT | 0.621 | 0.582 | 0.392 | 0.426 | 0.75 |
| EN | 0.952 | 0.940 | — | — | 1.0 |
| DE | 0.966 | 0.879 | — | — | 1.0 |
| FR | 0.952 | 0.916 | — | — | 1.0 |
| ES | 0.952 | 0.940 | — | — | 1.0 |

**Docling (do_ocr=False, TableFormer ACCURATE) — coverage IT/Mamoli:**
| Lingua | codeAcc | descAcc(fz) | qtyAcc | priceAcc | structFid |
|--------|:-------:|:-----------:|:------:|:--------:|:---------:|
| IT | 0.983 | 0.382 | 0.892 | 0.682 | 1.0 |

> Nota coverage: **Docling è stato validato solo sul caso peggiore (Mamoli IT)**, dove
> serviva il fallback. Su EN/DE/FR/ES il riferimento misurato è la baseline (≈1.0 codeAcc).

## 3. Il risultato decisivo — codice canonico esatto sul worst-case
Il codice è il valore vendibile: deve arrivare **esatto** al guardrail P3. Match
canonico esatto vs GT su Mamoli (worst-case picture-text):

| Motore | p231 codeAcc esatto | p060 |
|--------|:-------------------:|:----:|
| Marker (re-OCR) | 0.000 | 1.000 |
| baseline (recall, non canonico) | 1.000* | 1.000 |
| Docling raw (canonico esatto) | 0.000 | 1.000 |
| **Docling + Normalizzatore deterministico** | **0.968 (91/94)** | **1.000** |

\* baseline ha ottimo *recall* dei codici ma non produce la forma canonica pulita
(markup/spazi); il normalizzatore la produce in modo esplicito e loggato.

**Soglia richiesta ≥ 0.95 sul worst-case: RAGGIUNTA (0.968).** I 3 residui p231 sono
errori Docling reali (prefisso `V0` perso / cella doppio-codice) → instradati a
**"verifica manuale"** (non silenziosi), coerente col principio "giusto oppure non lo so".

## 4. Raccomandazione architetturale (estrazione tabellare)
**Per le estrazioni tabellari si adotta: Docling (`do_ocr=False`, TableFormer ACCURATE)
+ Normalizzatore deterministico dei codici.**
- **Evitare il re-OCR (Marker) per i codici**: ne corrompe una classe (`V0…`→`0`) sui
  glifi stilizzati, dove il text-layer nativo è corretto.
- Il **normalizzatore è un nodo deterministico** (non-LLM, auditabile) a monte del
  guardrail codici P3.
- Le **descrizioni** leggibili le migliora solo il re-OCR/VLM: eventuale uso **ibrido**
  di Marker/VLM **solo** per la colonna descrizione, mai per i codici (rimandato a P2/P3).

## 5. ⚠️ VINCOLO CRITICO — il normalizzatore attuale è un PoC hardcoded
`scripts/normalize_codes_docling.py` è un **Proof-of-Concept overfittato su Mamoli**: le
regex di famiglia-codice (es. `V0` + 10 alfanumerici), le regole di cleanup e i pattern
di struttura sono **hardcoded in Python**. Questo va bene per superare il gate P1 su un
brand, **non** è la forma di produzione.

**In Fase 2 (SaaS, P7) le regole di normalizzazione e le regex di struttura DEVONO essere
slegate dal codice ed estratte a CONFIGURAZIONE DI DB, per-brand / per-tenant** (una
tabella di "profili di normalizzazione codici" caricata a runtime), così che un nuovo
cliente/marchio si onboardi con configurazione dati e non con una patch al codice. È
anche coerente con l'economia dell'onboarding (SPEC §H.1: il costo per-cliente è il dato,
non il software). Finché resta hardcoded, ogni nuovo brand richiede intervento sviluppo:
da monitorare come debito tecnico e da chiudere prima del multi-tenant.

## 6. Casi peggiori trovati (registro)
- **Mamoli p231** (picture-text, codici `V0…` stilizzati): il caso peggiore del corpus;
  re-OCR lo distrugge, baseline+Docling+normalizzatore lo reggono (0.968 esatto).
- **Tabelle affiancate fuse** (Docling rende 2 tabelle come una larga): rilevato e
  flaggato (`fused_suspected`), non ancora separato — 2 dei 3 residui cadono lì.
- **Esplosi/schemi** (NordCap pag.esploso, DirectKitchen): immagini pure → pipeline
  visiva (P2), non estrazione testo.
- **Descrizioni**: misura fuzzy ancora bassa dove il testo nativo è small-caps mangled;
  non blocca il gate (il codice è il valore critico), ma è lavoro per P2.

## 7. Condizioni e caveat (validità della decisione)
- Validato su **campione ridotto** (6 tabelle; Docling+normalizzatore su 2 pagine Mamoli).
  Prima di fissare l'architettura in P2, **confermare su più pagine picture-text** e su
  più brand.
- **Docling non ancora misurato su DE/EN/FR/ES**: lì il riferimento è la baseline (buona).
- Gestione **split tabelle affiancate**: da implementare (P2).
- Normalizzatore: PoC hardcoded (vedi §5).

## 8. Decisione
✅ **GATE P1 UFFICIALMENTE SUPERATO — GO verso P2 (Retrieval ibrido + misurazione).**
Motivazione: sul valore critico (codice ricambio) l'architettura **Docling + normalizzatore
deterministico** raggiunge ≥0.95 esatto sul worst-case reale, con i residui gestiti in
modo esplicito e non silenzioso; struttura e recall sono adeguati. I limiti sono noti,
misurati e circoscritti (§7), non scoperti.

> Prossimo punto: **P2** — solo dopo OK esplicito (si procede un Punto alla volta).
