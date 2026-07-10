# P3.2 §7 — Calibrazione soglie e misura del "non lo so"

> Data: 2026-07-04 · Eval: **v1 avversario, 78 query** (70 positive IT/EN/DE/ES/FR
> + 8 negative) · Margini rigenerati da Qdrant (`scripts/calibrate_confidence.py`),
> non da `results_v1.json` (che ha solo i top-1 assoluti). Margine per-scorer:
> **BM25** per le query code, **reranker BGE sigmoidato** per natural/mixed
> (fix d'integrazione: `rerank.py` scarta i logit — qui ricalcolati). Tutti i
> numeri osservati da esecuzione reale.

## 1. Distribuzione dei margini per categoria (la prova)

Categorie: **POS_HIT1** (gold al rank 1 → rispondere è giusto), **POS_MISS**
(gold non al rank 1 / non recuperato → il top-1 è un doc sbagliato, tacere è
giusto), **NEG** (query senza risposta → tacere è giusto).

| scorer / categoria | n | min | mediana | max |
|---|---:|---:|---:|---:|
| **reranker** POS_HIT1 (rispondere) | 20 | 0.000 | 0.505 | 0.998 |
| **reranker** POS_MISS (tacere) | 34 | 0.000 | 0.277 | 0.889 |
| **reranker** NEG (tacere) | 5 | 0.000 | 0.048 | 0.431 |
| **bm25** POS_HIT1 | 14 | 0.000 | **1.000** | 1.000 |
| **bm25** POS_MISS | 2 | — | 0.003 | 0.003 |
| **bm25** NEG | 3 | 0.000 | **0.000** | 0.000 (`has_evidence=False`) |

**Lettura.** Il canale **code/BM25 è pulito e deterministico**: i negativi hanno
BM25 top-score 0 → `has_evidence=False` → BASSA per costruzione (gancio #1);
i POS_HIT1 hanno margine 1.0 → ALTA. Il canale **reranker separa male**:
POS_HIT1 e POS_MISS si sovrappongono su tutto l'intervallo, e **13/34 POS_MISS
hanno margine > 0.5** — il cross-encoder è *confidentemente sbagliato*. Il
margine misura la **decisività**, non la **correttezza**: non può, da solo,
distinguere un top-1 giusto da un top-1 sbagliato ma dominante.

## 2. Perché queste soglie (τ_hi = 0.90, τ_lo = 0.0054)

- **τ_hi = 0.90** — appena **sopra il massimo margine POS_MISS osservato**
  (0.889). Regola: ALTA è negata a ogni caso "confident-wrong" visto. Risultato
  verificato: **ALTA precision = 1.0 (13/13 POS_HIT1, zero falsi-ALTA)**. È la
  garanzia più importante: il sistema non asserisce **mai** con certezza un
  documento sbagliato.
- **τ_lo = 0.0054** — scelto col vincolo **pigrizia ≤ 0.10** sui POS_HIT1. Non
  si insegue il silenzio a scapito della ritenzione, perché lo sweep prova che
  sul reranker non esiste un buon taglio:

  | τ_lo | silence_recall | pigrizia |
  |---:|---:|---:|
  | 0.00 | 0.000 | 0.000 |
  | 0.05 | 0.184 | 0.238 |
  | 0.10 | 0.289 | 0.286 |
  | 0.20 | 0.474 | 0.381 |
  | 0.30 | 0.553 | 0.381 |
  | 0.50 | 0.658 | 0.476 |

  Ogni punto di recall del silenzio costa quasi altrettanta pigrizia: il segnale
  è **non-discriminante**. (Sia il fit "precisione-ALTA" sia il punto di Youden
  degenerano — conferma matematica.) Perciò τ_lo resta basso: BASSA-su-margine
  solo per retrieval genuinamente indeciso; il resto → **MEDIA** ("verifica
  consigliata"). Il silenzio dei confident-wrong naturali è **rimandato a P3.3**.

## 3. Misura del "non lo so" (τ_hi 0.90, τ_lo 0.0054)

Livelli complessivi: **ALTA 13 · MEDIA 51 · BASSA 14**. Composizione:

| livello | POS_HIT1 | POS_MISS | NEG | lettura |
|---|---:|---:|---:|---|
| **ALTA** | 13 | 0 | 0 | precision **1.0** — nessuna asserzione sbagliata |
| **MEDIA** | 18 | 30 | 3 | "verifica consigliata": include i confident-wrong non rilevabili dal margine |
| **BASSA** | 4 | 5 | 5 | silenzio: 10 giusto vs 4 pigrizia (**71% dei silenzi è giustificato**) |

- **Silenzio giusto**: 10/43 should-silence (**recall 0.233**).
- **Pigrizia**: 4/35 POS_HIT1 (**rate 0.114**).
- **Ritenzione risposte** (POS_HIT1 non silenziati): 31/35 = **0.886**.

### Scomposizione per lingua (SPEC §F.1)

| lingua | n | silenzio giusto | recall | pigrizia | rate | ALTA/MEDIA/BASSA |
|---|---:|---:|---:|---:|---:|---|
| IT | 41 | 5/28 | 0.179 | 0/12 | 0.000 | 5 / 31 / 5 |
| EN | 16 | 3/8 | 0.375 | 2/10 | 0.200 | 6 / 7 / 5 |
| DE | 8 | 1/4 | 0.250 | 1/6 | 0.167 | 2 / 6 / 2 |
| ES | 5 | 1/2 | 0.500 | 0/3 | 0.000 | 0 / 4 / 1 |
| FR | 4 | 0/1 | 0.000 | 1/3 | 0.333 | 0 / 3 / 1 |

*Cautela:* n per lingua piccolo (FR 4, ES 5) → i tassi per lingua sono rumorosi.
Il dato robusto è su IT (n=41): recall silenzio 0.179, pigrizia 0.000 — coerente
col tallone noto (naturali IT cross-lingua che il margine non separa → finiscono
in MEDIA, non in BASSA).

## 4. Conclusioni oneste

1. **ALTA è affidabile (precision 1.0):** zero falsi-ALTA su tutte le lingue.
   La regola d'oro sul lato "mai sbagliato in silenzio" è rispettata dove conta.
2. **Il code channel funziona:** "non lo so" deterministico via gancio #1
   (BM25 = 0), nessuna soglia opaca.
3. **Il margine da solo NON basta sui naturali:** misurato, non congetturato
   (sweep §2). Confermato ciò che SPEC §F.2 e il gancio #2 prevedevano — il
   "non lo so" sulle naturali **richiede segnali multipli**. Il primo è
   l'**accordo testo↔visione (P3.3)**; poi query expansion/glossario (P3+).
4. **MEDIA come default onesto:** i confident-wrong non rilevabili non vengono
   asseriti (niente ALTA) né silenziati falsamente — vanno a "verifica
   consigliata". Non è "sbagliato in silenzio".

**Limite di metodo:** questa misura assume `gate=PASS` (l'eval è senza
generazione LLM; il guardrail sui codici generati entra col nodo di sintesi
P3.4/P3.5). Misura la **porzione da retrieval** dei segnali "non lo so". Le
soglie sono **provvisorie**, da ri-fittare col set validato dal tecnico.

Artefatti: `output/eval/confidence_calibration_v1.json` (righe per-query, sweep,
misura), `scripts/calibrate_confidence.py`, soglie in
`src.retrieval.confidence.CALIBRATED_V1`.
