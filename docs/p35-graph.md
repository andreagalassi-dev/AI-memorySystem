# P3.5 — Grafo LangGraph: chiusura di P3

> Data: 2026-07-04 · `src/graph/pipeline.py` · LangGraph come **FSM
> deterministica**. Nodi puri dove possibile, client iniettato (Deps). Numeri e
> output da esecuzione reale (Qdrant + Ollama qwen2.5:14b su).

## 1. Il flusso cablato

```
router → retrieval →(errore? finalize) confidence →(BASSA? refuse)
      fusion(diagnostica) → synthesis →(errore? finalize) guardrail → finalize
```
Orchestrazione sottile sopra funzioni già testate: `classify_query` (P2.4),
`routed_rr` (P2), `confidence_level` (P3.2), `page_agreement` (P3.3, diagnostico),
`check_answer`+`strip_citations` (P3.1), `synthesize` (P3.4). L'unico passo
non-deterministico è la generazione, ristretta da grounding+citazione+guardrail.

## 2. Due validazioni deterministiche attorno all'LLM

- **pre** (`confidence`): livello P3.2 e **se chiamare l'LLM** (BASSA → rifiuto
  senza spendere il modello);
- **post** (`guardrail`): P3.1 su `strip_citations(answer)`;
- **`reconcile`** (pura): `system_error`→BASSA "non disponibile"; rifiuto→BASSA;
  **rifiuto dell'LLM** anche con retrieval MEDIA→BASSA (seconda linea "non lo
  so"); guardrail **BLOCK**→"verifica manuale" BASSA; **VERIFY**→MEDIA; **PASS**
  → livello invariato.

## 3. Fail-closed di rete (requisito, verificato)

`retrieval_node`/`synthesis_node` catturano ogni eccezione (Qdrant/Ollama giù,
timeout) → `system_error` → BASSA "Sistema non disponibile", loggato in `audit`.
Il grafo non esplode. Verificato da test iniettando client che sollevano
(`ConnectionError`, `TimeoutError`).

## 4. Evidenza dallo smoke REALE end-to-end

| query | livello | esito |
|---|---|---|
| "codice della coppia di maniglie?" | **MEDIA** | `20001E (Mamoli…, pag. 57)` — guardrail PASS |
| `V09156800007` | **MEDIA** | risposta+prezzo+pag. 231 + "(verifica consigliata)": guardrail ha visto **evidenza OCR/degraded** (gancio #3) |
| "codice del reattore a fusione fredda?" | **BASSA** | LLM rifiuta → "Non ho evidenza sufficiente" |

La difesa in profondità è viva end-to-end: fonte+pagina sempre citate, guardrail
che declassa su OCR inaffidabile, "non lo so" quando l'evidenza non regge.

## 5. DoD di P3 — confronto finale

- **Risposta con fonte+pagina** ✅ (citazioni obbligatorie, payload doc/page).
- **Codici allucinati BLOCCATI** ✅ (guardrail P3.1 nel grafo; test
  `test_guardrail_block_su_codice_allucinato` + `strip_citations` per lo scope).
- **"Non lo so" su segnali oggettivi** ✅ (P3.2 pre-gate + rifiuto LLM +
  fail-closed; mai auto-confidenza LLM).
- **Grafo deterministico** ✅ (LangGraph FSM, percorso fisso, `audit` tracciabile).

**Debiti onesti a P3+ (documentati):** promozione MEDIA→ALTA rimandata (P3.3:
accordo retrieval-retrieval non sicuro, serve un 3° segnale indipendente);
soglie `CALIBRATED_V1` provvisorie (da ri-fittare col set del tecnico);
guardrail conservativo su quantità in prosa (P3.4 W030); tallone naturali IT
cross-lingua (leva: query expansion/glossario, P3+).

Artefatti: `src/graph/pipeline.py`, `tests/test_graph.py` (15 test),
smoke reale sopra.
