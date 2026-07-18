# GATE P1 — Decisione go/no-go estrazione tabelle

> **Esito: ✅ GO** — data 2026-07-10, branch `feature/fase1-model-multiplexer`.
> Riferimenti: `CLAUDE.md` (P1 è un GATE), `docs/SPEC.md` §D/§P1, commit fix `cd6d7fb`.
> Script riproducibile: `scripts/evaluate_gate_p1.py` (`--discover`, `--gate`).

## Contesto

P1 chiede due numeri sul corpus reale — qualità OCR e **accuratezza estrazione
tabelle** — e una decisione go/no-go scritta. Il binario 1.2 usa **GLM-OCR** (VLM)
per estrarre righe `codice→descrizione→prezzo` da PostgreSQL, con un **guardrail
deterministico**: ogni codice deve esistere verbatim nel text-layer/OCR, altrimenti
la riga è respinta (Fail-Closed).

## Cosa è emerso indagando (root cause di un falso allarme)

Lo spike iniziale (`scripts/spike_gate_p1_glm.py`) dava `grounding_validated=0` su
3 PDF e sembrava un fallimento del gate. **Era un falso allarme di disegno**:
girava solo sulle **prime 5 pagine** (front-matter: copertine, legende, indici
visivi) — mai su un vero listino. Indagando:

1. **L'adapter GLM-OCR è corretto** (ricetta = model card ufficiale). Su una vera
   pagina-listino (Mamoli p230) estrae tabelle strutturate.
2. **MA GLM-OCR corrompe i codici** anche su render born-digital pulito:
   `V09011500C01` → `V0901150001` (perde la `C`), `V09012000G01` → perde la `G`.
   Descrizioni e prezzi giusti; il **codice** — chiave del guardrail e del merge
   Fase 2 — sbagliato. È il "fallimento silenzioso" che il progetto vieta.

## Correzione architetturale adottata

Le tabelle **born-digital** (text-layer nativo affidabile) non devono passare dal
VLM: i codici sono già esatti sul PDF. Introdotto:

- **`TextTableExtractor`** (`src/ingestion/extraction/text_table.py`): parser
  block-aware deterministico su PyMuPDF `get_text("words")`. Codici **esatti
  verbatim** (zero inferenza), righe dubbie in reject lane (Fail-Closed).
- **Routing** (`src/ingestion/run_massive_extraction.py::route_table_page` +
  `text_extractor_factory`): tabella born-digital → estrattore testuale;
  **scansione vera** → GLM-OCR. Cablato in `scripts/run_full_corpus.py`.

Prova anti-corruzione (test d'integrazione reale su Mamoli p230): codici
`V09011500C01`, `V09012000G01`, `V09008000001` estratti **verbatim**, prezzo
`90.00` corretto, ≥80 righe. Suite completa **679 test verdi**.

## Risultati del GATE su vere scansioni

Discovery OCR su **tutti i 31 documenti** (`--discover`): **nessuna vera
tabella-prezzi scansionata** nel corpus. Le pagine "scansione + tabella" sono
**esplosi, copertine, note, foto** — falsi positivi di `is_table_page` (scatta sui
box tratteggiati/bordi). Sono target del binario **visivo (PixelRAG 1.1)**, non
dell'estrazione strutturata.

GLM-OCR su 4 vere scansioni (MPS, `--gate`):

| Pagina | proposte | validate | respinte |
|---|---|---|---|
| opacmare p59 (idraulica IT/EN) | 2 | **0** | **2** |
| kbs p4 (quadro DE) | 0 | 0 | 0 |
| branson p5 (esploso EN) | 0 | 0 | 0 |
| serrmac p20 (macchina IT) | 0 | 0 | 0 |

Dove GLM-OCR ha proposto qualcosa (opacmare p59), erano **non-codici**
("prodotti / products / produits", "Raccordo a T inox…"): il guardrail li ha
respinti **100% (2/2)** con `codice_non_ancorato`. **Zero falsi accetti.**

## Decisione: GO

- ✅ **Il rischio reale è chiuso**: la corruzione-codici di GLM-OCR colpiva le
  tabelle born-digital, che ora bypassano il VLM e ottengono codici esatti.
- ✅ **Guardrail fail-safe verificato**: respinge il 100% dell'output non ancorato
  del VLM; nessun codice corrotto raggiunge la SSOT.
- ✅ **Qualità OCR** (VisionOcr nativo) sufficiente a fornire la verità di grounding.

### Caveat onesto

L'accuratezza-codice di **GLM-OCR su vere scansioni di listino** resta **non
misurata** — non per mancanza di test, ma perché **quel dato non esiste nel corpus
attuale**. GLM-OCR resta un fallback con rete di sicurezza provata. Se un cliente
fornirà un listino puramente scansionato, rilanciare `scripts/evaluate_gate_p1.py`
`--gate` per misurare la code-accuracy per lingua prima di fidarsi delle sue righe.
