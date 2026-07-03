# P2.8 — Recall globale e chiusura della roadmap P2

> Data: 2026-07-03 · Indici finali: **aike_chunks 32.966 punti** (testo, 54 PDF
> + XLSX) · **aike_pages_visual 1.035 pagine** (multivettori MaxSim) ·
> Eval: **v1 avversario, 78 coppie** (70 positive IT/EN/DE/ES/FR + 8 negative)
> + eval visiva 15 query. Tutti i numeri osservati da esecuzioni reali.

## 1. Classificatore v2 (punto cieco P2.5 chiuso)

Regola di banda (img 0.10–0.45 + chars 100–350 → grafica, 11/12 nel GT di
banda) su GT esteso a **113 pagine** (16 nuove, 2 duplicati contraddittori
rimossi): **DEV 0.918, HOLDOUT 0.962, recall grafica holdout 1.000**.
Riclassificazione: grafica 831→931, route colpali 878→**1033** (+172 pagine
docling dual estratte, +153 visive indicizzate, 0 fallimenti). Limite
documentato: `n_draw_items` non aiuta su questo corpus (items ≡ oggetti path);
il caso branson-p68 (esploso da 54 path grassi con >300 chars) resta noto.

## 2. Eval v1 — l'avversarialità è PROVATA, non promessa

- 58 nuove coppie sui brand nuovi + 12 carryover v0 (expected allargati) + 8
  negative. Query scritte in gergo d'officina, molte **cross-lingua per
  costruzione** (query IT su descrizioni EN/DE: l'officina italiana coi
  manuali esteri).
- **Overlap lessicale query↔target: mediana 0, max 2** (2 coppie annotate:
  termini senza sinonimi reali). BM25 non è favorito in partenza: misurato,
  nel report JSON.
- Metodologia dichiarata: fogli 0–4 scritti dai render; restanti
  dall'identità del pezzo con gate meccanico di overlap (≤1) — la garanzia è
  il validatore, non la promessa. 2 errori di trascrizione codici dai render
  corretti in corsa (W058 annotato).

## 3. I numeri del DoD P2 (strategia finale `routed_rr`)

**Complessivo (70 positive): R@1 0.486 · R@5 0.600 · MRR 0.542**
(routed senza reranker: R@1 0.371 · MRR 0.450)

| tipo | routed R@1 | **routed_rr R@1** | n |
|---|---:|---:|---:|
| code | 0.737 | **0.842** | 19 |
| mixed | 0.500 | **0.875** | 8 |
| natural | 0.186 | **0.256** | 43 |

| lingua (routed_rr) | R@1 | R@5 | MRR | n |
|---|---:|---:|---:|---:|
| IT | 0.316 | 0.421 | 0.368 | 38 |
| EN | 0.625 | 0.750 | 0.688 | 16 |
| DE | 0.667 | 0.778 | 0.735 | 9 |
| ES | 0.750 | 1.000 | 0.833 | 4 |
| FR | 1.000 | 1.000 | 1.000 | 3 |

**Lettura onesta.** Il confronto col v0 (R@1 0.944 su 234 chunk; 0.704 su 33k)
non è una regressione del sistema ma dell'*asticella*: il v1 è 2× più duro per
costruzione (overlap 0, cross-lingua). Il dato strutturale: le query
**same-language** vanno bene (EN/DE/ES/FR 0.62–1.00 R@1); il tallone è la
**naturale IT su descrizioni EN terse** (boccola↔bushing su 6 parole di desc)
— i 28 miss del routed_rr sono quasi tutti lì. Le leve sono note e a P3+:
espansione/traduzione query (LLM), arricchimento desc via VLM, glossario di
dominio bilingue. Il codice esatto — il valore critico del dominio — regge:
**0.842 R@1 / 0.947 R@10** anche con marker multilingua e su 33k distrattori.

## 4. Reranker: ADOTTATO (criterio soddisfatto sui numeri)

BGE-reranker-v2-m3, **implementazione transformers nativa** (il wrapper
FlagEmbedding usa API rimosse in transformers 5.x — TERZA incompatibilità
silenziosa di dipendenza del progetto, dopo EasyOCR e i LoRA colpali).
Cross-encoder sui top-20 routed, **mai sulle query code** (decisione del
router). A/B sulla stessa eval: **R@1 +0.115, MRR +0.092, migliora OGNI tipo
e OGNI lingua**. Costo dichiarato: latenza mediana **0.764s** vs 0.058s
(accettabile per il PoC; da rivalutare a P5 con la UI).

## 5. Soglia «non lo so» (conferma su v1)

Il canale **sparse sulle query codice resta perfettamente separabile**
(max negativo 0.0 vs min positivo 11.17): il "non lo so" sui codici
inesistenti è deterministico. Tutte le altre scale (dense/rrf/reranker)
hanno overlap → servono i segnali multipli di P3, come previsto dalla SPEC.

## 6. Canale visivo (aggiornato a 1.035 pagine)

Eval visiva su collection allargata: R@1 0.200 · R@3 0.533 · MRR 0.367
(stabile rispetto alle 882 pagine: le 153 aggiunte non degradano). Limiti e
rimedi come da `docs/p27-visual.md` (fusion e captioning a P3).

## 7. Chiusura P2 — confronto col DoD SPEC

*«recall@k e correttezza misurati sul corpus reale, scomposti per lingua»*:
- recall@k per lingua ✅ (tabelle sopra, eval avversario, 5 lingue);
- correttezza-data-evidenza → P3 (P2 senza LLM per vincolo di progetto);
- filtro pagine ✅ (P2.5+v2), embedding visivi sulle sole grafiche ✅ (P2.7),
  ricerca ibrida ✅ (P2.3/2.4), reranking ✅ (P2.8), set di valutazione ✅
  (v1, 78+15 coppie — da validare col tecnico: TODO permanente).

**La sub-roadmap P2.5–P2.8 è chiusa.** Debiti aperti con owner: fusione
testo↔visione e segnali "non lo so" multipli (P3), correttezza (P3),
validazione eval dal tecnico (cliente), miglioramento cross-lingua (P3+,
richiede LLM/VLM), normalizzatore→config DB per-tenant (P7, già impostato
con brands.json).
