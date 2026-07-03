# P3.2 — Segnali oggettivi "non lo so" → livelli di sicurezza — Design

> Data: 2026-07-03 · Punto roadmap: **P3.2** (secondo micro-step di P3).
> Riferimenti: SPEC §F.2 (gestione incertezza), §F.1 (metriche per lingua),
> `docs/p3-handoff.md` §4 (ganci). Vincolo: ingestion intoccabile.
> Regola d'oro: **giusto oppure "non lo so", mai sbagliato in silenzio.**

## 1. Scopo e vincolo

Un motore **deterministico** che, dati i segnali oggettivi del retrieval,
assegna un **livello di sicurezza** — **ALTA** / **MEDIA** / **BASSA** — alla
risposta (SPEC §F.2). ALTA → risposta diretta con fonte; MEDIA → risposta +
"verifica consigliata"; BASSA → **dichiara "non lo so"**.

**Vincolo non negoziabile (§F.2):** nessun segnale viene dall'auto-confidenza
dell'LLM. Solo segnali oggettivi del sistema. Il gancio #2 del handoff prova
che le scale *assolute* (dense/RRF/reranker) hanno overlap → ci si appoggia su
segnali **relativi** (margine) e **booleani** (gate, evidenza, OCR).

**Fuori scopo P3.2:** il calcolo pieno dell'accordo testo↔visione (`A`) →
**P3.3** (qui è input `Optional`, `None`); il grafo (P3.5); l'LLM (P3.4).

## 2. I segnali (deterministici, ognuno con fonte)

| segnale | tipo | definizione | fonte |
|---|---|---|---|
| **G** — gate guardrail | enum | PASS / VERIFY / BLOCK | P3.1 (`guardrail.check_answer`) |
| **R** — evidenza presente | bool | code: `BM25_top>0`; altri: ≥1 hit | gancio #1 |
| **M** — margine top1–top2 | float [0,1] | **relativo** `(s1−s2)/s1` sui punteggi finali | vedi §3 |
| **O** — evidenza inaffidabile | bool | top hit con `ocr_source`/`status=degraded` | gancio #3 |
| **A** — accordo testo↔visione | Optional | agree/disagree/None | gancio #6 → **P3.3** |

## 3. Margine M — scelte e edge case (approvate)

`M = (s1 − s2) / s1`, **relativo** e channel-agnostic (scalare valori assoluti
tra canali diversi non funziona — gancio #2). Regole:

- **0 hit** → `M = 0.0` (irrilevante: `R` assente forza BASSA).
- **1 solo hit valido** (s2 assente) → `M = 1.0` (**massima separazione**).
- **≥2 hit** → `(s1−s2)/s1`, clampato in [0,1].
- **s1 ≤ 0** (es. BM25 top 0 sui code inesistenti) → `M = 0.0` (gancio #1 →
  BASSA).

**Su quali punteggi si calcola M (edge case RRF).** M usa i punteggi **finali
della strategia di produzione `routed_rr`**: reranker (cross-encoder) per
natural/mixed, BM25 per code. I punteggi reranker sono **ben spalmati** → `τ`
stabile. *Attenzione:* `search_unified` oggi ritorna `routed` (RRF) per il
canale testo (search.py:104); i punteggi RRF sono **compressi** (somme di rank
inversi) e darebbero un margine schiacciato. Perciò:
1. il nodo di retrieval del grafo (P3.3/P3.5) deve alimentare P3.2 con i
   risultati **rerankati** (`routed_rr`), non RRF grezzi;
2. `τ_hi`/`τ_lo` sono fittati **per-scorer** (BM25-code vs reranker-natural/
   mixed) — le distribuzioni non sono commensurabili. Se un giorno si usasse
   RRF grezzo, richiede un `τ` proprio (rischio documentato).

**Requisito d'integrazione sui punteggi (rerank.py:44-48).** Il reranker
calcola `logits` ma **scarta i punteggi**: ritorna gli hit riordinati col loro
`.score` RRF **originale** (non monotòno col nuovo ordine). Perciò il nodo di
retrieval del grafo (P3.3/P3.5) deve **preservare il logit del reranker e
applicargli `sigmoid`** → probabilità in [0,1], su cui il margine relativo è
ben definito. Canale code: i punteggi BM25 (routed sparse) sono già sugli hit e
non-negativi. `compute_margin` (core P3.2) resta agnostico e guarda solo la
lista di punteggi non-negativi ricevuta, con guardia `s1 ≤ 0 → 0.0`.

## 4. Modello a due strati (fail-closed, valutato dall'alto)

**Hard gates** (portano giù, mai su):
1. `G==BLOCK` **oppure** `R` assente → **BASSA** ("non lo so").
2. `G==VERIFY` **oppure** `O` → cap a **MEDIA**.

**Soft score** (solo se nessun hard gate ha deciso BASSA):
3. `M ≥ τ_hi` e `A ≠ disagree` → **ALTA**.
4. `τ_lo ≤ M < τ_hi`, oppure `A == agree` → **MEDIA**.
5. `M < τ_lo` e `A ≠ agree` → **BASSA**.

## 5. Matrice di calibrazione (approvata)

| G | R | O | M | A | → livello |
|---|---|---|---|---|---|
| BLOCK | – | – | – | – | **BASSA** |
| PASS/VERIFY | assente | – | – | – | **BASSA** |
| VERIFY | presente | – | – | – | **MEDIA** |
| PASS | presente | vero | – | – | **MEDIA** |
| PASS | presente | falso | ≥ τ_hi | ≠ disagree | **ALTA** |
| PASS | presente | falso | ≥ τ_hi | disagree | **MEDIA** |
| PASS | presente | falso | [τ_lo,τ_hi) | qualsiasi | **MEDIA** |
| PASS | presente | falso | < τ_lo | agree | **MEDIA** |
| PASS | presente | falso | < τ_lo | ≠ agree | **BASSA** |

`τ_hi`/`τ_lo` sono gli unici numeri liberi → §7.

## 6. Interfaccia pubblica (`src/retrieval/confidence.py`)

```
class Level(Enum): ALTA; MEDIA; BASSA
@dataclass Thresholds: tau_hi: float; tau_lo: float
@dataclass Signals: gate: str; has_evidence: bool; margin: float;
                    unreliable: bool; agreement: str | None
@dataclass ConfidenceReport: level: Level; signals: Signals; reason: str

compute_margin(scores: Sequence[float]) -> float          # §3, edge case inclusi
extract_signals(unified, guardrail_report, *, is_code) -> Signals   # da search_unified
confidence_level(signals, thresholds) -> ConfidenceReport # matrice §5, reason auditabile
```

Nessun LLM importato. `agreement` sempre `None` in P3.2 (P3.3 lo popolerà).

## 7. Calibrazione e misura (chiusura DoD P3.2 — richiede eval)

Sub-step successivo alla TDD del core puro (analogo al lookup Qdrant di P3.1):
- **fit di `τ_hi`/`τ_lo`** per-scorer su `output/eval/results_v1.json` scegliendo
  il punto sulla curva con l'asimmetria della regola d'oro (preferire un
  falso-MEDIA a un falso-ALTA);
- **misura "non lo so"** su eval v1, **scomposta per lingua** (§F.1):
  **silenzio giusto** (BASSA su negativa o miss reale) vs **silenzio per
  pigrizia** (BASSA con gold nel top-k). Precisione/recall del "non lo so".

## 8. Piano di test (TDD — core puro, soglie iniettate)

`compute_margin`: 0 hit → 0.0; 1 hit → 1.0; ≥2 → relativo; s1≤0 → 0.0;
clamp; ordine decrescente assunto (o ordinato internamente).
`confidence_level`: **una riga di test per riga della matrice §5**, con soglie
iniettate note; monotonia (margine ↑ non abbassa il livello a parità di resto);
`reason` non vuota; `A` a `None` non promuove mai ad ALTA da sola.
`extract_signals`: da un dict `search_unified` sintetico + `GuardrailReport`
→ `Signals` corretti (R da BM25>0 per code; O da warnings del top hit).

## 9. DoD di P3.2

- [ ] `compute_margin` gestisce 0/1/≥2 hit e s1≤0 (test verdi).
- [ ] `confidence_level` implementa la matrice §5 riga-per-riga; fail-closed.
- [ ] Auto-confidenza LLM **mai** usata (solo segnali oggettivi).
- [ ] `A` resta `Optional`/`None` (logica visiva rimandata a P3.3).
- [x] Suite verde; commit.
- [x] **τ calibrati + misura "non lo so" per-lingua (§7)** → `docs/p32-nonloso-
      calibration.md`. Esito: τ_hi 0.90 (ALTA precision 1.0, zero falsi-ALTA),
      τ_lo 0.0054; margine reranker **misurato non-separabile** → silenzio dei
      confident-wrong naturali rimandato a P3.3 (come previsto da §F.2/gancio #2).
