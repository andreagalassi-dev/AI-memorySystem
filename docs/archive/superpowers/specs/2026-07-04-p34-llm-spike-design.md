# P3.4 — Spike LLM locale + sintesi ancorata (text-reasoner) — Design

> Data: 2026-07-04 · Punto roadmap: **P3.4**. Riferimenti: SPEC §C (due modelli,
> ragionatore solo-testo), §D (fusion→sintesi), §F.2. Vincolo: ingestion
> intoccabile. Regola d'oro: **giusto oppure "non lo so", mai sbagliato in
> silenzio.**

## 1. Scopo e confine

Accendere il **ragionatore testuale** locale e verificare che sappia fare
**sintesi ancorata**: risposta *solo* dall'evidenza recuperata, con **citazione
obbligatoria** (doc+pagina) o rifiuto esplicito. **L'LLM non valida**: i codici
li controlla P3.1, la confidenza la decide P3.2. L'LLM è un **sintetizzatore
vincolato**; ogni sua uscita ripassa dai guardrail deterministici (difesa in
profondità).

**Scelte fissate (utente):** Ollama; **Qwen2.5-14B-Instruct default assoluto**
(`qwen2.5:14b`); il 32B è solo piano-B d'emergenza se il 14B crolla sul
cross-lingua (Apple Silicon: un 32B quantizzato = 20+GB, generazione lenta).
**VLM generativo rimandato** (fuori scope).

## 2. Componenti

- `src/backend/clients/ollama_client.py` — wrapper sottile (come `qdrant_client`):
  `generate(system, prompt, model, options)`; opzioni sintesi sicura
  `temperature=0.1`, `seed` fisso, `num_ctx`, `num_predict` limitato.
- `src/retrieval/synthesis.py` — parti PURE (testabili senza LLM):
  - `format_evidence_xml(hits) -> str` — grounding in **tag XML**;
  - `SYSTEM_SYNTHESIS` — prompt di sistema (§3);
  - `build_prompt(query, hits) -> str` — assembla `<evidenza>…</evidenza>` +
    `<domanda>…</domanda>`;
  - `extract_citations(text) -> list[(doc, page)]` — parsing `(doc, pag. N)`;
  - `synthesize(client, query, hits, model)` — chiama l'LLM (integrazione).
- `scripts/spike_llm.py` — harness dello spike (§4).

## 3. Prompt di sistema (XML tags) — versione finale

Il grounding è confinato in tag XML: la domanda dell'utente non può confondersi
con l'evidenza.

```
Sei un assistente tecnico per ricambi manifatturieri. Rispondi ESCLUSIVAMENTE
usando il contenuto tra <evidenza>...</evidenza>. Non usare conoscenza esterna.

REGOLE INVIOLABILI:
1. Se la risposta non è deducibile dall'<evidenza>, scrivi ESATTAMENTE:
   "Non ho evidenza sufficiente per rispondere." e fermati.
2. Ogni affermazione cita la fonte tra parentesi: (doc, pag. N), dagli
   attributi di <documento>. Nessuna citazione = non affermare.
3. NON inventare codici ricambio: riporta un codice SOLO se compare
   letteralmente in un <documento>. Nel dubbio, ometti il codice.
4. Rispondi nella lingua della <domanda>, anche se l'evidenza è in altra lingua.
5. Conciso e fattuale: nessuna supposizione, nessun riempitivo.
```

Formato dell'evidenza iniettata:
```
<evidenza>
  <documento doc="rotax_912_914_IPC" pag="22" lang="EN">
    877615 — PISTON PIN ... (qty 1)
  </documento>
  ...
</evidenza>
<domanda>che codice ha lo spinotto del pistone?</domanda>
```

Il livello P3.2 decide **a monte**: BASSA → "non lo so" senza chiamare l'LLM;
MEDIA → sintesi + framing "verifica consigliata"; ALTA → sintesi diretta.

## 4. Spike: gate di valutazione (osservazione dell'output reale)

Harness su ~12 query eval con **evidenza reale** (retrieval `routed_rr`), su
`qwen2.5:14b`. Gate — i primi due **deterministici**, riusano i guardrail:

| gate | come | tipo |
|---|---|---|
| **No codici allucinati** | `guardrail.check_answer(output, evidenza)` ≠ BLOCK | deterministico (P3.1) |
| **Citazione obbligatoria** | ogni `(doc, pag.)` citato ∈ evidenza; ≥1 su risposte non-rifiuto | deterministico |
| **Obbedienza "non lo so"** | evidenza vuota/debole → stringa di rifiuto esatta | quasi-det. |
| **Cross-lingua** | query IT su desc EN → risposta IT corretta | umano (output mostrato) |
| **Latenza** | s/risposta su Apple Silicon | misurato |

**Criterio di decisione (come per il reranker: il più piccolo che basta):** il
14B è adottato se passa i due gate deterministici e regge il cross-lingua con
latenza accettabile. Il 32B si valuta **solo** se il 14B fallisce il grounding
o il cross-lingua.

## 5. Piano di test (TDD — parti pure)

`format_evidence_xml`: struttura `<documento doc=".." pag=".." lang="..">`,
escaping, evidenza vuota → `<evidenza></evidenza>`; niente campi None grezzi.
`extract_citations`: estrae `(doc, pag. N)`, tollerante a spazi; nessuna
citazione → lista vuota. `build_prompt`: contiene entrambi i blocchi XML e la
query dentro `<domanda>`. La chiamata LLM è integrazione (spike, non unit test).

## 6. DoD di P3.4

- [x] Wrapper Ollama + sintesi con prompt XML; parti pure testate (16 verdi).
- [x] Spike 14B su evidenza reale; **gate misurati**: no-codici-allucinati 6/7
      (0 allucinazioni del modello; 1 BLOCK = guardrail su quantità), citazione
      7/7, rifiuto 2/2, latenza 6.2s. Report: `docs/p34-llm-spike.md`.
- [x] **Raccomandazione: adottare qwen2.5:14b** (no escalation 32B) — dai numeri.
- [x] Confine rispettato: l'LLM non valida. Requisito P3.5: guardrail su output
      via `strip_citations`. Commit.
