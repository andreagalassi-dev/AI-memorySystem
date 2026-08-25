# Stato del progetto — recap epiche A→H (aggiornato 2026-08-11, v2 corretta)

> **Nota di allineamento (25/08/2026):** fotografia dell'11/08/2026, non lo stato attuale (per
> lo stato ricostruito dai commit vedi `15_piano_prodotto.md`, allineato il 25/08). La
> collezione Qdrant `aike_chunks` citata sotto era corretta allora; il 24/08/2026 la
> produzione è passata a `aike_chunks_v2` (`aike_chunks` resta solo come rollback).

Documento vivo di stato/gap, complementare al piano `15_piano_prodotto.md`.
Legenda: ✅ fatto · ⚠️ parziale · 🔲 da fare.

> NB v2: la v1 (basata sul testo del piano, rimasto indietro) sbagliava su C e
> D-B.4. Corretta dopo analisi read-only del git log + degli indici reali.

## Stato per epica

| Epica | Stato | Sintesi |
|---|---|---|
| **A** — Un solo sistema | ✅ | main unico, UI Chainlit, cervello Ollama gemma4+fallback qwen2.5, smoke gate |
| **B** — Misura (il metro) | ✅ | golden gate per-lingua, truth set rifinito, precision ALTA alta |
| **C** — Estrazione di massa | ✅ | ingestion unificata (`ingest_document_full`/`run_ingest_full.py`), 3 binari fail-closed; tutti i 142 PDF coperti in ≥1 modalità |
| **D** — Correttezza risposta | ✅ | D1-D6 + arco D-B chiuso; **D-B.4 (Docling in produzione) FATTO** (stage1/2a/2b/2c) |
| **E** — Visivo performante | ✅ | reranker +26pt, gate anti-sbagliato, router fix — pushato (`1e0eb06`) |
| **F** — Memoria 3 livelli + feedback | 🔲 | **prossima** (contesto conversazionale + 3 livelli + feedback) |
| **H** — Cascata + web | 🔲 | non iniziata — "mai un'affermazione senza fonte" (visione avanzata) |
| **G** — Doppio profilo hardware | ⚠️ | gate hardware reranker fatto; profilo NVIDIA all'arrivo del DGX |

## Copertura reale (142 PDF nel corpus)

| Tipo domanda | Indice | Coperti |
|---|---|---|
| Testo/narrativa | aike_chunks | 119 / 142 |
| Prezzi/codici | estrazione.righe_listino | 96 / 142 (169.567 righe) |
| Esplosi/visivo | aike_pages_visual | 93 / 142 |

**`corpus − testo − visivo − prezzi = 0`**: NESSUN PDF è fuori da tutti gli indici.
Ogni documento è interrogabile in almeno una modalità. I numeri 119/96/93 non sono
"buchi" ma la naturale composizione dei contenuti: brochure→testo, cataloghi→
testo+esplosi, listini→testo+prezzi.

**Unico residuo minore:** pochi *listini* sono in testo ma senza prezzi estratti
(copertura strutturata parziale su singoli doc). Follow-up opzionale, non un blocco.

## Perché C sembrava "incompleta" (correzione storica)

Il piano `15` descrive C1 SOSPESA (il pilota rompeva il gate narrativo) e C2.b sospesa
(2 incidenti di corruzione). **Ma dopo D-B.4 (29/07) C è stata RIAPERTA e portata a
termine** con un approccio nuovo: **ingestion unificata fail-closed** dei 3 binari
(`ingest_document_full`, runner ripartibile `run_ingest_full.py`, batching upsert
visivo, flag `--force/--branches`). Commit `c1ea806`→`17824cd`. È il motivo per cui
oggi la copertura è quella sopra. Il testo del piano su C è quindi storia, non stato.

## Cosa resta da fare (scaletta)

**Prossima epica (prerequisiti soddisfatti — corpus stabile e coperto):**
1. **F** — Memoria a 3 livelli + **contesto conversazionale** (requisito utente 19/07:
   oggi `answer` è stateless, non capisce "e per il modello più grande?").

**Follow-up minori / opzionali:**
2. Estrarre i prezzi dei pochi listini oggi solo-testo (copertura strutturata).
3. **E-residui** — cross-lingua (embedding non-EN), disambiguazione 2b (feasibility
   negativa sul segnale score, serve redesign query-level).

**Futuro dichiarato:**
4. **H** — cascata + ricerca web ("mai un'affermazione senza fonte").
5. **G** — profilo NVIDIA all'arrivo del DGX.

## Note di metodo (invarianti)
- Un'epica/punto alla volta, con DoD e STOP; commit piccoli.
- Reviewer verifica OGNI claim coi tool; misurare **end-to-end via `answer()`**, non il
  ramo isolato (lezione E: il router può non mandare al ramo atteso).
- **Verificare il codice/git reale, non il testo del piano** (può essere stale — vedi
  la svista D-B.4/C di questa v1→v2).
- Mai abbassare soglie di sicurezza (tau_hi=0.90, VISUAL_SCORE_FLOOR=0.20).
- Modifiche ai file: le fa l'ESECUTORE; il reviewer orchestra e verifica.
