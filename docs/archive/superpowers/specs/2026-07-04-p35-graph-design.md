# P3.5 — Orchestrazione a grafo (LangGraph) — Design

> Data: 2026-07-04 · Punto roadmap: **P3.5** (chiusura di P3). Riferimenti:
> SPEC §C (grafo deterministico), §D, §F.2. Vincolo: ingestion intoccabile.
> Regola d'oro: **giusto oppure "non lo so", mai sbagliato in silenzio.**

## 1. Scopo

Cablare in un unico flusso deterministico i pezzi già scritti e testati:
**router → retrieval → confidence → fusion(diagnostica) → sintesi → guardrail →
finalize**. LangGraph è usato come **FSM deterministica**, non come agente
libero: il percorso è fisso, la generazione è l'unico passo non-deterministico
(e ristretto da grounding + citazione + guardrail).

Il grafo è **orchestrazione sottile** su funzioni pure già testate:
`classify_query`, `compute_margin`+`confidence_level`, `page_agreement`,
`check_answer`+`strip_citations`, `synthesize`. I nodi decisionali sono puri;
solo `retrieval` e `synthesis` fanno I/O, col **client iniettato** (DB/LLM
confinati, nodi testabili con fake).

## 2. State e tipi (`src/graph/pipeline.py`)

```python
class Evidence(NamedTuple):        # compatto, immutabile, disaccoppia dal payload
    doc: str; page: int; lang: str
    code: str | None; desc: str | None; text: str | None
    qty: float | None; price: float | None; type: str
    status: str | None; warnings: list[str]
    score: float                   # BM25 (code) | reranker-SIGMOID (natural/mixed)

class GraphState(TypedDict, total=False):
    query: str
    route: str; code_token: str | None; route_reason: str
    evidence: list[Evidence]
    margin: float; has_evidence: bool; unreliable: bool; level: str
    agreement: str | None          # DIAGNOSTICO (UI/audit), non entra nel livello
    answer: str | None; citations: list[tuple[str, int]]
    guardrail_gate: str            # PASS | VERIFY | BLOCK
    system_error: bool             # fail-closed di rete
    final_answer: str; final_level: str
    audit: list[str]
```

**Dependency injection** — `build_graph(deps)` con:
`deps.retrieve(query)->list[Evidence]`, `deps.synthesize(query, evidence)->str`,
`deps.catalog_lookup(key)->bool`, `deps.thresholds` (= `CALIBRATED_V1`),
`deps.cross_probe(query)->list[(doc,page)]|None` (visivo lazy, opzionale).

## 3. Flusso

```
START → router → retrieval → «errore?»
   sì → finalize (Sistema non disponibile)
   no → confidence → «level?»
          BASSA      → refuse → finalize
          ALTA/MEDIA → fusion(lazy, diagnostico) → synthesis → «errore?»
                          sì → finalize (Sistema non disponibile)
                          no → guardrail → finalize
finalize → END
```

**Due validazioni deterministiche attorno all'LLM:** pre (`confidence`: livello +
se chiamare l'LLM; BASSA → rifiuto senza spendere il modello), post
(`guardrail`: P3.1 su `strip_citations(answer)`).

**`finalize` — riconciliazione pura (testabile in isolamento):**
- `system_error` → `final_answer="Sistema non disponibile. Riprova più tardi."`,
  `final_level="BASSA"`;
- rifiuto (level BASSA) → `REFUSAL`, `BASSA`;
- guardrail **BLOCK** → `"Verifica manuale: la risposta contiene un codice non
  confermato dall'evidenza."`, `BASSA` (mai mostrare un codice non confermato);
- guardrail **VERIFY** → livello capato a `MEDIA` + nota "verifica consigliata";
- **PASS** → livello P3.2 invariato (ALTA/MEDIA).

## 4. Fail-closed di rete (requisito utente, prima classe)

`retrieval_node` e `synthesis_node` **catturano ogni eccezione** (Qdrant/Ollama
giù, timeout): impostano `system_error=True`, aggiungono all'`audit` la causa, e
il flusso salta a `finalize` → **BASSA "Sistema non disponibile"**. Il grafo non
esplode mai. Nota: `guardrail_node` è già fail-closed a monte (`check_answer`
degrada a VERIFICA se `catalog_lookup` solleva). Il cross-probe di `fusion` è
non-fatale: un errore azzera solo il segnale diagnostico `agreement`, non ferma
il grafo.

## 5. Piano di test (TDD)

- **`reconcile` puro:** system_error, refuse, BLOCK, VERIFY, PASS×(ALTA/MEDIA).
- **Nodi con fake:** retrieval mappa payload→Evidence; confidence setta level;
  synthesis setta answer+citazioni; guardrail setta gate.
- **Fail-closed:** `retrieve` che solleva → system_error + BASSA + audit;
  `synthesize` che solleva → system_error + BASSA + audit.
- **End-to-end (build_graph + fake deps):** (a) percorso MEDIA → risposta+fonte;
  (b) BASSA → refuse e **synthesize NON chiamato**; (c) guardrail BLOCK →
  "verifica manuale"; (d) Qdrant-down → Sistema non disponibile; (e) Ollama-down
  → Sistema non disponibile.
- **Smoke integrazione (opzionale):** grafo reale su 1-2 query se Qdrant+Ollama su.

## 6. DoD di P3.5 (e chiusura P3)

- [x] Grafo LangGraph cablato; nodi puri dove possibile, client iniettato.
- [x] `finalize` riconcilia livello × guardrail; percorso "non lo so" attivo.
- [x] **Fail-closed di rete** verificato (Qdrant/Ollama giù → BASSA, no crash).
- [x] Guardrail sull'output via `strip_citations`; codici allucinati bloccati.
- [x] Suite verde; commit. DoD P3 complessivo: risposta con fonte+pagina,
      codici allucinati bloccati, "non lo so" su segnali oggettivi.
