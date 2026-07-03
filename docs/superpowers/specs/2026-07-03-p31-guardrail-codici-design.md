# P3.1 — Guardrail deterministico sui codici (NON-LLM) — Design

> Data: 2026-07-03 · Punto roadmap: **P3.1** (primo micro-step di P3).
> Riferimenti: SPEC §D (validazione deterministica), §C (nodo del grafo),
> `docs/p3-handoff.md` §4 (ganci misurati). Vincolo: ingestion intoccabile.
> Regola d'oro: **giusto oppure "non lo so", mai sbagliato in silenzio.**

## 1. Scopo e mandato

Un nodo **non-LLM** che, data una risposta candidata dell'LLM e l'evidenza
recuperata, verifica che **ogni codice citato esista letteralmente nella
tabella recuperata** (SPEC §D). Un codice che non esiste → la risposta viene
**bloccata** o **degradata a "verifica manuale"**, mai passata in silenzio.

Il nodo **riusa** la normalizzazione GT-testata dell'ingestion
(`src/ingestion/codes.py`, `src/ingestion/brands.py`) — non ne inventa una
nuova — così la forma canonica del codice citato è identica a quella indicizzata
(stesso principio "identica indice/query" del BM25, `docs/p3-handoff.md`).

**Fuori scopo P3.1:** il grafo LangGraph (P3.5), l'LLM (P3.4), i segnali "non lo
so" multi-segnale sulle naturali (P3.2), la fusion testo↔visione (P3.3).
Qui si costruisce solo il verificatore deterministico + i suoi unit test.

## 2. Il compito, in una frase

> Per ogni token code-shaped nella risposta, decidere uno di tre verdetti —
> **ESISTE** / **VERIFICA MANUALE** / **BLOCCO** — con `reason` auditabile, e
> aggregare in un gate complessivo della risposta.

## 3. Architettura: tre stadi + aggregazione

Tre funzioni pure, ognuna testabile in isolamento (CLAUDE.md), più un
orchestratore.

### Stadio A — Estrazione candidati (generosa, poi filtro anti-misura)

Obiettivo: catturare ogni token che *potrebbe* essere un codice, senza
catturare misure/date/numeri di contesto.

1. **Gate positivo (riuso router):** un token è candidato se matcha una
   famiglia nota (via `brands.all_extract_patterns()`, tolleranti a
   spazi/markup), **o** `DEFAULT_CODEY` (alfanumerico con cifre+lettere len≥4,
   oppure ≥5 cifre pure), **o** `DEFAULT_CODEY` su `canonical(token)`.
   *Quest'ultima aggiunta (emersa in TDD)* cattura i codici **puramente
   numerici scritti coi separatori** (`340-006-734` → canonical `340006734` →
   codey): senza di essa sfuggirebbero all'estrazione → PASS silenzioso.
1b. **Anti-troncamento (emersa in TDD):** uno span di famiglia è valido solo se
   non taglia un token-codice contiguo più lungo. Confine pulito = bordo
   testo / char non-codice, oppure un altro span adiacente (caso doppio fuso
   `V0..V0..`). Senza questa regola l'allucinato `0USBX44` verrebbe troncato
   allo `0USBX4` valido → allucinazione mascherata. Span contenuti in altri
   span (es. `\d{9}` dentro un V0) vengono scartati (si tiene il massimale).
2. **Filtro anti-misura (sottrattivo, ad alta confidenza).** Un candidato che
   **non matcha una famiglia nota** viene scartato (trattato come prosa) se
   ricade in una di:
   - **suffisso unità** (lista config-driven, case-insensitive):
     `V VAC VDC W kW A mA Hz kHz Ω ohm mm cm m km µm Nm bar Pa °C °F K % g kg
     L ml rpm dB Ø " '` — forma `\d+([.,]\d+)?\s?<unità>`, incluse gamme
     `\d+-\d+<unità>` → esclude `230V`, `12-24V`, `50Hz`, `5mm`, `12 Nm`;
   - **forma dimensionale/filetto** `\d+\s?[xX×]\s?\d+` (`x` **tra cifre**, non
     tocca codici con X interna tipo `0USBX4`) → esclude `M6x16`, `12x16`;
   - **forma data** `\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}` e ISO
     `\d{4}-\d{2}-\d{2}`.

   **Regola non negoziabile:** una **famiglia riconosciuta vince sempre** sul
   filtro. Un allucinato `0USBXV` (finisce in V ma matcha `0US[0-9A-Z]{3}`)
   resta candidato e viene verificato. Il filtro rimuove solo misure di prosa
   *non* famiglia-shaped.

   *Asimmetria e rischio residuo (documentato):* l'esclusione è intenzionalmente
   stretta. Un falso-BLOCCO (misura scambiata per codice) sarebbe safe ma
   fastidioso; lo evitiamo. Il rischio inverso — un allucinato *non-famiglia*
   con forma esatta di misura che sfugge alla verifica (falso-PASS) — è
   accettato come minimo e per i brand noti quasi-nullo (le famiglie catturano
   le forme reali dei codici). Rivalutabile con dati reali.

### Stadio B — Risoluzione profilo brand

Per ogni candidato, il profilo di normalizzazione è quello del **brand
dell'evidenza** (`brands.resolve(doc)` sui chunk recuperati), non indovinato.

### Stadio C — Scala di confronto a 3 gradini (asimmetrica verso la sicurezza)

Confronto del candidato contro i `code` dell'**evidenza recuperata** (in
memoria, non una nuova query):

| gradino | test | verdetto |
|---|---|---|
| 1. letterale | `base_cleanup(cand).upper() == base_cleanup(e).upper()` | **ESISTE** — case/markup/spazi non significativi |
| 2. variante-separatore | fallisce (1), ma `canonical(cand) == canonical(e)` | **VERIFICA MANUALE** — separatore potenzialmente significativo (Opzione A confermata) |
| 3. assente nell'evidenza | fallisce anche (2) su tutta l'evidenza | → lookup globale ↓ |

**Lookup globale** (solo per il gradino 3, gancio #4: payload index su `code`):
- filtro esatto Qdrant `code == base_cleanup(cand).upper()` su `aike_chunks`;
- **esiste nel catalogo** ma non nell'evidenza → **VERIFICA MANUALE** (miss del
  retrieval, non allucinazione);
- **non esiste** nel catalogo → **BLOCCO: "codice non a catalogo / allucinato"**.
  Per le query `code`, coincide col gancio deterministico **BM25 top-score 0.0**
  (`docs/p3-handoff.md` §4.1).

`O/0` e `I/1` **non** si foldano: `canonical("OUSBX4") ≠ canonical("0USBX4")` →
confusione OCR → BLOCCO. Foldarle rischierebbe il falso-PASS.

### Aggregazione — gate della risposta

Verdetto complessivo = il **peggiore** tra i codici (fail-closed):
- un solo **ABSENT** → gate **BLOCCO**;
- nessun ABSENT ma almeno un **AMBIGUO / MISS_RETRIEVAL** → gate **VERIFICA**;
- evidenza vincente con `status=degraded` o warning `ocr_source` (gancio #3)
  → gate almeno **VERIFICA**, a prescindere dai codici;
- tutti **ESISTE**, oppure **nessun codice** citato → gate **PASS**.

## 4. Interfaccia pubblica (`src/retrieval/guardrail.py`)

```
@dataclass CodeVerdict:
    raw: str; canonical: str
    status: Literal["EXISTS","AMBIGUOUS","MISS_RETRIEVAL","ABSENT"]
    matched_evidence_code: str | None
    reason: str

@dataclass GuardrailReport:
    gate: Literal["PASS","VERIFY","BLOCK"]
    verdicts: list[CodeVerdict]
    reason: str

extract_candidates(text, evidence_docs) -> list[str]        # Stadio A (+B profilo)
is_measurement(token, profile) -> bool                       # filtro anti-misura
verify_code(cand, evidence, client) -> CodeVerdict           # Stadio C + lookup globale
check_answer(answer_text, evidence, client) -> GuardrailReport  # orchestratore
```

`evidence` = i chunk recuperati (payload con `code`, `doc`, `status`,
`warnings`). Il `client` Qdrant serve solo al lookup globale del gradino 3.
Nessun LLM importato in questo modulo.

## 5. Piano di test (TDD — il set avversario è il fixture)

Unit test scritti **prima** dell'implementazione. Il fixture combina codici
**reali campionati da `aike_chunks`** (casi ESISTE positivi, ancorati al dato) e
perturbazioni sintetiche pattern-valide (i "quasi-giusti"). Matrice:

| # | citato | evidenza | atteso | categoria |
|---|---|---|---|---|
| 1 | `V01234567890` | `V01234567890` | ESISTE | identità |
| 2 | `v01234567890` | `V01234567890` | ESISTE | solo case |
| 3 | `**V01234567890**` | `V01234567890` | ESISTE | markup |
| 4 | `V0 1234 5678 90` | `V01234567890` | ESISTE | rumore-spazi (rejoin) |
| 5 | `V01234567891` | `V01234567890` | BLOCCO | sostituz. 1 cifra |
| 6 | `V01234567809` | `V01234567890` | BLOCCO | trasposizione |
| 7 | `V0ZZZZZZZZZZ` | assente | BLOCCO | ben formato, non a catalogo |
| 8 | `0USBX4` | `0USBX4` | ESISTE | identità 0US |
| 9 | `0USBX5` | `0USBX4` | BLOCCO | sostituz. 1 char |
| 10 | `OUSBX4` (lettera O) | `0USBX4` (zero) | BLOCCO | OCR O/0 |
| 11 | `0USBX` / `0USBX44` | `0USBX4` | BLOCCO | troncamento / extra |
| 12 | `123456789` | `123456789` | ESISTE | identità num-9 |
| 13 | `123-456-789` | `123456789` | VERIFICA | variante-separatore |
| 14 | `123456780` | `123456789` | BLOCCO | sostituz. 1 cifra |
| 15 | `M6-16` | `M616` (corto) | VERIFICA | separatore significativo |
| 16 | `M616` | `M6-16` (corto) | VERIFICA | simmetrico #15 |
| 17 | `V01234567890` | assente in evidenza, **in catalogo** | VERIFICA | miss retrieval (lookup globale) |
| 18 | `V0AAAA1111BBV0CCCC2222DD` | 2 codici reali | estrai 2 + verifica ciascuno | doppio fuso |
| 19 | risposta senza codici | — | PASS | non blocca prosa |
| 20 | codice in chunk `status=degraded`/`ocr_source` | — | VERIFICA | gancio #3 |
| 21 | "coppia 12 Nm, attacco 1/2" | — | PASS | misure NON estratte |
| 22 | "alimentazione 12-24V DC" | — | PASS | range unità non estratto |
| 23 | "vite M6x16" | — | PASS | forma filetto/dim. non estratta |
| 24 | "diametro 5mm, 50Hz, 230V" | — | PASS | unità non estratte |
| 25 | "revisione del 12/05/2024" | — | PASS | data non estratta |
| 26 | `0USBXV` (famiglia, finisce V) | assente | BLOCCO | guard anti-sovraesclusione |

Test aggiuntivi: `is_measurement` in isolamento; `extract_candidates` conta e
forma dei token; aggregazione gate (peggiore-vince); `reason` non vuota per ogni
verdetto (auditabilità).

## 6. Note d'implementazione

- Modulo nuovo `src/retrieval/guardrail.py`; nessuna dipendenza dal grafo (che
  non esiste ancora) → P3.5 lo importerà come nodo di validazione.
- Lista unità come costante di modulo (`_UNITS`); esternalizzabile a config se
  cresce (YAGNI: inline ora).
- Lookup globale con `client.scroll`/`count` + filtro esatto su payload `code`
  (index già presente su `aike_chunks`). In test unitari il client è mockato o
  si usa un fixture in-memory; un test d'integrazione opzionale colpisce Qdrant
  reale se disponibile.
- Guardrail **fail-closed**: qualsiasi eccezione nel lookup → VERIFICA (mai
  PASS silenzioso).

## 7. DoD di P3.1 (sottoinsieme del DoD P3)

- [ ] Codici allucinati (sostituz./trasposiz./OCR O-0) → **BLOCCO** verificato
      dai test.
- [ ] Varianti-separatore → **VERIFICA MANUALE** (Opzione A).
- [ ] Misure/date/unità → **non estratte** (nessun falso BLOCCO).
- [ ] Codici reali (identità/case/markup/rejoin) → **ESISTE** (nessun falso
      blocco), ancorati a codici veri di `aike_chunks`.
- [ ] `reason` auditabile su ogni verdetto; gate fail-closed.
- [ ] Suite unit test verde; commit di chiusura P3.1.
