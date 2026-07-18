# Handoff P1 → P2 — per la prossima istanza

> Gate P1 **superato** (vedi `docs/p1-gate-decision.md`). Questo file ti fa partire su
> **P2 (Retrieval ibrido + misurazione)** senza rileggere la chat. Regola del progetto:
> **un Punto alla volta**, plan mode prima di scrivere codice, e OK esplicito dell'utente
> prima di iniziare P2.

## 1. Stato esatto dei dati
- **Markdown pronti per il retrieval con CODICE NORMALIZZATO ESATTO:**
  `output/spike/docling_normalized/raw/<table_id>/<table_id>.md`.
  Contengono le righe-tabella col codice ricambio in forma **canonica esatta**
  (es. `V09156800007`), prodotta dal normalizzatore deterministico (P1.5+).
- **Copertura attuale:** solo il campione worst-case **Mamoli** (`MAMOLI_P231_IT`,
  `MAMOLI_P060_IT`). NON è ancora l'intero corpus: l'estrazione full-corpus è lavoro di
  ingestion da fare in P2 (rigirare Docling+normalizzatore su tutte le pagine tabellari).
- Altri output di spike (riferimento/confronto): `output/spike/baseline/` (tutti i 6 file,
  PyMuPDF4LLM), `output/spike/marker/` (6 pagine campione), `output/spike/docling/` (raw
  Mamoli). Ground truth annotato: `data/ground_truth/<table_id>/expected.csv`.
- ⚠️ **`output/` e `data/` sono GITIGNORED** (dati locali). Sono **rigenerabili** con gli
  script in `scripts/` (`extract_*.py`, `normalize_codes_docling.py`,
  `evaluate_ingestion_p1.py`). Non contarci come se fossero nel repo.

## 2. Nota tecnica sul CHUNKING (vincolo non negoziabile per P2)
Quando splitterai i `.md` per indicizzarli:
- **I codici ricambio NON devono MAI essere spezzati a metà** da un chunk boundary.
- **Ogni chunk deve contenere la RIGA INTERA della tabella** (codice ↔ descrizione ↔
  quantità ↔ prezzo insieme), ancorata a **pagina/documento d'origine** per la citazione.
- Niente chunking testuale "a finestra" cieco sulle tabelle: distruggerebbe la relazione
  riga-colonna. Il codice è l'unità atomica (vedi `docs/ingestion-challenges.md` §3 e
  SPEC §D). Il codice è già canonico nei file di §1: preservalo intatto nel chunk.

## 3. Obiettivo del PRIMO spike di P2
Implementare un **indicizzatore Qdrant con retrieval IBRIDO: Dense + Sparse (BM25)**.
- Motivo: la **sola ricerca semantica (dense) fallisce sui codici alfanumerici** tipo
  `V09156800007` / `AB-12345-X`; serve il canale **lessicale esatto (BM25)** per matcharli
  (SPEC §D "alta priorità", `docs/ingestion-challenges.md` §4).
- Stack deciso: **Qdrant** (collezione per tenant), embedding **BGE-M3** (dense+sparse),
  reranker **BGE-reranker-v2** prima dell'LLM. Backend Qdrant già up (P0, `/health`).
- Primo spike concreto: indicizza i chunk-riga di §1 in Qdrant con payload duale
  (vettore denso + sparse/BM25), poi verifica che una query con un **codice esatto** lo
  recuperi via canale lessicale e una query in linguaggio naturale lo recuperi via denso.
- Poi (P2 pieno): filtro pagine grafiche vs testuali, embedding visivi (ColPali) solo
  sulle grafiche, set di valutazione ~50–100 coppie, **recall@k + correttezza per lingua**.

## Puntatori utili
- Decisione di gate + numeri: `docs/p1-gate-decision.md`
- Definizione metriche + eval: `docs/p1-metrics.md`, `scripts/evaluate_ingestion_p1.py`
- Architettura estrazione scelta: **Docling(do_ocr=False,ACCURATE) + normalizzatore**;
  evitare re-OCR sui codici. Il normalizzatore è un **PoC hardcoded** (regex `V0…`
  overfittate su Mamoli) → in Fase 2 va esternalizzato a **config DB per-brand/tenant**
  (vedi `p1-gate-decision.md` §5).
- Sfide di ingestion pre-gate: `docs/ingestion-challenges.md`
