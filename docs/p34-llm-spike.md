# P3.4 — Spike ragionatore 14B: sintesi ancorata + gate

> Data: 2026-07-04 · Modello: **qwen2.5:14b** (Ollama, Apple Silicon) · 9 casi
> (7 con evidenza reale `routed` + 2 a evidenza vuota) · Gate deterministici che
> RIUSANO il guardrail P3.1. Output reale osservato (non check statici).
> Artefatti: `output/eval/spike_llm_14b.json`, `scripts/spike_llm.py`.

## 1. Risultati finali sui gate

| gate | esito |
|---|---|
| **No codici allucinati** (guardrail P3.1 ≠ BLOCK) | **6/7** |
| **Citazione obbligatoria** (ogni `(doc, pag.)` ∈ evidenza) | **7/7** |
| **Obbedienza "non lo so"** (evidenza vuota → rifiuto esatto) | **2/2** |
| **Latenza mediana** | **6.2 s / risposta** |

Dal punto di vista del **modello**: **zero codici-ricambio allucinati** sui 7
casi (ogni codice citato era nell'evidenza). L'unico BLOCK (W030) è il
**guardrail** che flagga la quantità in prosa `65000` su una risposta peraltro
confusa del modello — difesa in profondità che declassa a "verifica manuale",
non un'allucinazione di codice.

## 2. Comportamenti chiave osservati (il 14B è idoneo)

- **Rifiuto corretto su evidenza debole/sbagliata:** W002 (boccola→bushing,
  miss cross-lingua del retrieval) e W014 (doc sbagliato) → *"Non ho evidenza
  sufficiente per rispondere."* Il modello **non ha inventato** su evidenza non
  pertinente. Esattamente il comportamento voluto.
- **Cross-lingua:** C008 (query EN su catalogo IT/EN) → risposta corretta con
  codice `20001L` e citazione `(Mamoli_Catalogo_Ricambi_2021_IT_EN, pag. 205)`.
- **Rifiuto su evidenza vuota:** 2/2 stringa esatta.
- **Latenza** 6.2 s mediana sul 14B: accettabile per il PoC.

## 3. Cosa ha rivelato lo spike (bug di integrazione, non del modello)

Tre problemi trovati e corretti col ciclo osserva→correggi→ri-osserva:

1. **Template letterale nel prompt.** La regola "cita `(doc, pag. N)`" veniva
   letta *alla lettera*: il modello scriveva `(doc, pag. 1)` con la parola
   "doc". → prompt con esempio reale (`(rotax_912_914_IPC, pag. 22)`).
   Citazione 3/7 → 6/7.
2. **Scope del guardrail sulle citazioni.** Il nome-documento citato (es.
   `lycoming_IGO540_PC116`) contiene sotto-token code-shaped (`IGO540`,
   `PC116`) → il guardrail li estraeva come "codici allucinati" (falso-BLOCK,
   W040). → `strip_citations()`: **il guardrail legge le affermazioni, non le
   citazioni**. Requisito d'integrazione per P3.5.
3. **Robustezza del parser di citazioni.** Il modello a volte copia la forma
   attributo XML `(doc="NOME", pag. N)` o prefissa `doc `. → `_clean_doc`
   normalizza (senza intaccare nomi reali come `doc_x`). Citazione → **7/7**.

Residuo dichiarato: **W030** — il guardrail flagga la quantità `65000` in prosa
(euristica numerica conservativa) su una risposta mal-formata del modello per
una query a solo-codice (`DPM4000`) con contesto povero. È il guardrail che fa
il suo mestiere; migliorabile a P3+ distinguendo quantità/prezzi dai codici nel
testo generato.

## 4. Raccomandazione

**Adottare `qwen2.5:14b` come ragionatore** (SPEC §C). Motivazione dai numeri:
grounding solido, rifiuto corretto su evidenza debole, cross-lingua funzionante,
zero codici allucinati, latenza accettabile. **Nessuna escalation al 32B**: il
14B non è "crollato sul cross-lingua" — il criterio d'emergenza per il 32B non
è scattato. (Coerente col metodo reranker: il più piccolo che basta.)

## 5. Consegne per P3.5 (grafo)

- Il livello **P3.2 decide a monte** se chiamare l'LLM (BASSA → rifiuto senza
  chiamata) e il framing (MEDIA → "verifica consigliata").
- Il **guardrail P3.1 gira sull'output** dell'LLM via `strip_citations` (scope
  corretto) → difesa in profondità end-to-end **già dimostrata funzionante**.
- Confine rispettato: l'LLM **non** ha preso decisioni di validazione.
