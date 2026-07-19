# Sfide di ingestion sul corpus reale — note pre-gate per P1/P2

> **Stato:** note di analisi, **non decisioni**. L'architettura di ingestion si
> fissa a **P1** (GATE estrazione) e **P2** (retrieval), **dopo** uno spike di
> estrazione reale sui file ostici. Registrate qui a fine P0 per non perderle.
> Fonte: analisi del corpus reale (vedi `data/corpus_catalog.csv` da P0.6).

## Perché queste note esistono
Il corpus reale contiene file ostici (cataloghi ricambi enormi, esplosi, schemi
elettrici, testo multilingua affiancato). Un chunking testuale standard
distruggerebbe le tabelle e produrrebbe spazzatura sugli schemi. Queste sfide
vanno affrontate ai Punti giusti, non anticipate a P0.

## 1. Parser & estrazione tabelle — P1 (GATE)
- File critici: `Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf` (~53 MB, tabelle
  alfanumeriche fitte), `Electrolux_Distinta_Ricambi.xlsx`,
  `DeliTools_SpareParts_List.xlsx`.
- Rischio: chunking testuale standard rompe la struttura riga-colonna.
- Da validare a P1: Marker (default) vs MinerU vs Docling sul corpus vero.
- Modi di fallimento silenziosi da testare (SPEC §D): celle unite, tabelle su
  più pagine lette come due, intestazioni ripetute, righe-totale/note scambiate
  per dati. Test dedicato `codice → descrizione → quantità` sull'originale.

## 2. Immagini / schemi / esplosi — P1 (estrazione) + P2 (indicizzazione)
- File: `NordCap_Esploso_Ricambi_DE.pdf` (esploso), schemi elettrici in
  `DirectKitchen_Forni_Schemi_IT_EN.pdf`.
- Obiettivo prodotto: mostrare l'immagine/schema **direttamente nella UI**.
- Approccio proposto (da validare): rendere le pagine grafiche ad **alta
  risoluzione** come **immagini statiche**, servirle via **FastAPI**; indicizzare
  a parte i **metadati testuali** (cartiglio, leader lines, codici sull'esploso)
  per poterle recuperare e restituire al frontend.
- Filtro pagine grafiche vs testuali (SPEC §D): solo le grafiche vanno nella
  pipeline visiva (controllo storage/costo, embedding tipo ColPali).

## 3. Strategia di chunking — P2
- I **codici ricambio** sono unità atomiche: mai spezzati a metà da un chunk.
- Chunk ancorati a **riga-tabella + pagina/documento** d'origine (citazione).
- Preservare il contesto codice ↔ descrizione ↔ quantità nello stesso chunk.

## 4. Dual-indexing (semantico + lessicale) — P2
- Payload duale per ogni chunk:
  - **semantico**: embedding denso (BGE-M3) su Qdrant;
  - **lessicale esatto**: BM25 per i codici alfanumerici tipo `AB-12345-X`
    (la sola ricerca vettoriale li gestisce male — SPEC §D "alta priorità").
- Reranking (BGE-reranker) prima di passare all'LLM.

## 5. Multilingua — trasversale
- IT/EN affiancati: `Silca_TriaxPro_Ricambi_Multilingua.pdf`,
  `DirectKitchen_Forni_Schemi_IT_EN.pdf`; DE: `NordCap_Esploso_Ricambi_DE.pdf`.
- Metriche di valutazione **separate per lingua** (SPEC §F.1), mai in media.

## Guardrail (ricordare a P3)
Ogni codice citato in risposta deve **esistere letteralmente** nella tabella
recuperata (validazione deterministica non-LLM). Vedi SPEC §D.
