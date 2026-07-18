# P3.3 §5 — Calibrazione δ e verdetto sulla promozione cross-modale

> Data: 2026-07-04 · Eval v1 · Cross-probe **pigro** (ColQwen2 solo sui 50 MEDIA)
> · `scripts/calibrate_fusion.py` → `output/eval/fusion_calibration_v1.json`.
> Gate non negoziabile: **ALTA precision ~1.0**. Numeri da esecuzione reale.

## 1. Risultato della calibrazione δ

Promozioni MEDIA→ALTA per accordo testo↔visione (top-1 testo vs top-3 visivo su
`(doc, page±δ)`), e ALTA precision risultante:

| δ | promozioni | buone (POS_HIT1) | **cattive** | ALTA precision |
|---:|---:|---:|---:|---:|
| 0 | 2 | 1 | **1** | 0.933 |
| 1 | 4 | 1 | **3** | 0.824 |
| 2 | 4 | 1 | 3 | 0.824 |

**Nessun δ ≤ 2 dà zero promozioni cattive** → per il gate approvato, la
promozione automatica **NON si adotta**.

## 2. Perché: il caso W014 è irriducibile

Le promozioni "cattive" NON sono tutte uguali:

- **W020, C008** (compaiono a δ=1): text-top e gold sono **stesso doc, pagina
  adiacente** (ave p2 vs gold p1; Mamoli p203 vs gold p204). Sono *corretti a
  livello-pagina*, contati "miss" solo perché l'eval è a livello-riga. Un δ=1 li
  cattura come si sperava (tabella↔pagina vicina).
- **W014** (compare già a δ=0): text-top = `(manzoni_comac_B, 24)`, gold =
  `(panciroli_garmec_BL200, 46)` — **documento proprio sbagliato**. E il
  **top-1 visivo è ESATTAMENTE `(manzoni_comac_B, 24)`**: le due modalità
  sbagliano *nello stesso identico modo*. L'accordo è massimo ma falso.

Verifica diretta: stringere la regola a **visual-top-1 + δ=0** non salva —
W014 e il buono W009 hanno **entrambi** visual-top-1 esatto, sono
**indistinguibili** dal segnale di accordo. Quando testo e visione convergono
sullo stesso errore, nessun δ/topk li separa.

## 3. Verdetto e decisione adottata

**La promozione cross-modale MEDIA→ALTA non si adotta.** L'accordo testo↔visione
misura la *consistenza* tra modalità, non la *correttezza*: un doppio-errore
concorde (W014) produce un falso-ALTA, che viola la priorità del progetto
(ALTA precision ~1.0). Adottarla scambierebbe +1 promozione buona per +1÷3
false certezze — l'esatto contrario della regola d'oro.

Conseguenze nel codice:
- `confidence_level` **ripristinata alla semantica P3.2**: l'accordo NON
  promuove (il ramo di promozione è rimosso; commento esplicativo nel codice).
  ALTA precision resta **1.0** come misurato in P3.2.
- `page_agreement` (src/retrieval/fusion.py) **resta**, testato, come segnale
  **diagnostico** (utile in UI: "testo e immagine concordano su doc+pagina") e
  come base per la fusione multi-segnale futura — **non** come promotore di
  livello.

## 4. Cosa serve davvero per promuovere (P3+)

Il δ non era il problema (la guardia varianza non è nemmeno stata sfiorata: il
limite è a δ=0). Il problema è che **due segnali correlati non bastano**: testo
e visione possono essere ingannati dalla stessa query. Per promuovere in
sicurezza servirebbe un terzo segnale **indipendente** e verificabile — es. il
**guardrail codici P3.1** (se la pagina promossa contiene *letteralmente* il
codice citato) o l'accordo con una tabella strutturata. Promozione condizionata
a un controllo deterministico, non a due retrieval concordi. Rimandato.

## 5. Valore consegnato da P3.3

1. Segnale di accordo `(doc, page)` implementato e testato (matching diretto:
   le due collection condividono stem `doc` e `page` interi).
2. Cross-probe pigro (ColQwen2 solo sui MEDIA) — architettura di risparmio
   GPU/latenza validata.
3. **Prova, dati alla mano, che l'accordo retrieval-retrieval non è un
   promotore sicuro** — protetta la garanzia ALTA precision 1.0. Un risultato
   negativo che *impedisce* una regressione silenziosa della fiducia.

Artefatti: `output/eval/fusion_calibration_v1.json`, `scripts/calibrate_fusion.py`,
`src/retrieval/fusion.py`.
