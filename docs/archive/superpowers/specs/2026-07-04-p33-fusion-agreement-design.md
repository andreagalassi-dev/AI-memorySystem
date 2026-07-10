# P3.3 — Fusion testo↔visione: segnale di accordo (A) — Design

> Data: 2026-07-04 · Punto roadmap: **P3.3**. Riferimenti: SPEC §D (fusion),
> §F.2 (incertezza), `docs/p3-handoff.md` §4.6, `docs/p32-nonloso-calibration.md`.
> Vincolo: ingestion intoccabile. Regola d'oro: **giusto oppure "non lo so".**
> **Priorità non negoziabile:** l'ALTA precision deve restare ~1.0 (misurata).

## 1. Scopo

Calcolare il segnale **A** (accordo testo↔visione) previsto da P3.2 e usarlo per
**promuovere MEDIA→ALTA** quando due modalità indipendenti concordano — senza
introdurre falsi-ALTA. È la leva per recuperare i molti MEDIA della
calibrazione P3.2 (dove il margine reranker da solo non separava).

**Fuori scopo:** grafo (P3.5), LLM (P3.4). Qui: funzione di matching pura +
cross-probe pigro + emendamento alla matrice + **calibrazione di δ**.

## 2. Il fatto che rende il matching diretto (dai payload reali)

Le due collection condividono l'**identità documento** e la **pagina**:
- `aike_chunks`: `doc` (es. `rotax_912_914_IPC`), `page` (int).
- `aike_pages_visual`: `doc` (stessa stringa), `page` (int), `render`.

Lo stem `doc` è **identico stringa-per-stringa** → il match è un confronto di
tuple `(doc, page)`, nessuna normalizzazione/fuzzy.

## 3. Funzione di accordo (pura, `src/retrieval/fusion.py`)

`page_agreement(text, visual, *, delta, topk=3) -> "agree" | "disagree" | None`
dove `text=(doc,page)` è il top-1 testuale e `visual=[(doc,page),…]` i top
visivi:

- **agree** — esiste un hit nei **primi `topk`** visivi con `doc==text.doc` e
  `|page−text.page| ≤ δ` (top-1 testuale corroborato nel top-k visivo: più
  stabile del top1↔top1 al rumore di ranking);
- **disagree** — nessun agree e il **top-1 visivo** ha `doc != text.doc`
  (documenti in conflitto);
- **None** — un canale vuoto, oppure stesso doc ma pagine lontane (segnale
  debole: non promuove, non demota).

`δ` = tolleranza pagina, **calibrata sui dati** (§5).

## 4. Cross-probe pigro + emendamento alla matrice

**Cross-probe pigro:** le query naturali instradano solo-testo (`visual_hits`
vuoto). Per ottenere A si interroga *anche* l'indice visivo (`search_visual`,
ColQwen2) **solo quando il livello P3.2 è MEDIA e potrebbe salire ad ALTA** —
mai su ogni query (risparmio GPU/latenza, approvato).

**Emendamento a `confidence_level` (additivo, non rompe P3.2):**
```
ALTA se:  (A != disagree e margin >= tau_hi)            # decisivo (P3.2)
      OR  (A == agree  e margin >= tau_lo)              # PROMOZIONE cross-modale
MEDIA se: margin >= tau_lo  oppure  A == agree
BASSA altrimenti
```
La promozione lifta **solo la banda MEDIA** (`tau_lo ≤ margin < tau_hi`): un
retrieval sotto `tau_lo` (near-tie, indeciso) resta MEDIA anche con agree.
`disagree` non demota (solo "non promuove"): la demozione si valuta solo se
misurata utile.

## 5. Calibrazione di δ (con la guardia sulla varianza)

Misura su eval v1 (`scripts/calibrate_fusion.py`): per ogni query positiva,
top-1 testuale (routed_rr) + top-k visivo (ColQwen2), accordo a `δ ∈ {0,1,2}`.
Per ciascun δ, tra le query **in banda MEDIA con A==agree**:
- **promozioni buone** = POS_HIT1 (gold al rank 1) → giusto salire ad ALTA;
- **promozioni cattive** = POS_MISS/NEG → falso-ALTA.

**Criterio d'adozione (gate):** scegliere il **δ più grande, ≤ 2**, che dà
**zero promozioni cattive** (ALTA precision resta 1.0) massimizzando le buone.
**Guardia (approvata):** se servisse **δ > 2** per promuovere in modo
significativo → **STOP**: un δ grande + top-k fabbrica "agree" casuali e fa
collassare la precisione. In tal caso si adotta un δ ristretto (0/1) che
promuove solo i manuali ben formattati (tabella↔esploso inline), lasciando il
resto a MEDIA. Se persino δ=0 produce promozioni cattive → la promozione **non
si adotta** (si riporta il perché), il segnale A resta solo diagnostico.

## 6. Piano di test (TDD — logica pura, soglie/δ iniettati)

`page_agreement`: agree esatto (δ=0); agree entro δ; NO agree oltre δ;
disagree su doc diverso; None su canale vuoto; None su stesso doc pagine
lontane; rispetto di `topk` (match oltre il top-k non conta).
`confidence_level` (promozione): banda MEDIA + agree → ALTA; banda MEDIA +
None/disagree → MEDIA (invariato); `margin<tau_lo` + agree → resta MEDIA (non
si promuove il near-tie); i test P3.2 esistenti restano verdi.

## 7. DoD di P3.3

- [x] `page_agreement` implementa il matching (doc,page,δ,topk); test verdi.
- [x] δ calibrato sui dati: **misurato che la promozione NON preserva ALTA
      precision 1.0** a nessun δ≤2 (caso W014, doppio-errore concorde) →
      **promozione NON adottata**; `confidence_level` ripristinata a P3.2.
      Report: `docs/p33-fusion-calibration.md`.
- [x] Garanzia ALTA precision 1.0 **protetta** (motivo del non-adottare
      documentato). Segnale A resta diagnostico. Guardia varianza mai sfiorata
      (limite già a δ=0). Commit.
