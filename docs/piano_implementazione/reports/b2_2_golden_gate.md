# Report Golden Gate B2.2 (Prima Run)

## Metriche per lingua
- **IT**: 6/30 (20.0%)
- **EN**: 2/6 (33.3%)
- **DE**: 1/4 (25.0%)
- **ES**: 1/1 (100.0%)

## Esiti Hard Floor
La prima run ha rivelato svariate violazioni del "hard floor" in merito ai fallimenti `WRONG_ALTA`.
Il sistema restituisce un livello di confidenza `ALTA` anche quando la citazione restituita non combacia con il documento/pagina atteso (o è assente). Questo rappresenta un fallimento DURO di sicurezza (il sistema non può essere confidente su un'allucinazione o su un documento errato).

**Violazioni riscontrate (WRONG_ALTA):**
- C001
- C002
- C003
- C004
- C006
- C007
- C008
- C009
- C010
- C011
- C012
- C013
- C015
- C_DE_01
- C_DE_02
- C_DE_03
- C_EN_01
- C_EN_02
- C_EN_03

Non ci sono state violazioni del floor `RBAC_LEAK` (i rifiuti sono avvenuti correttamente o il documento ristretto non è trapelato). Non ci sono stati fallimenti `REFUSAL` (il sistema ha rifiutato onestamente quando richiesto).

## Baseline
La baseline (`golden_baseline_v1.json`) è stata creata correttamente congelando gli esiti correnti, tuttavia il job di CI fallirà per via dei WRONG_ALTA.

> **Verdetto per il reviewer:** I `WRONG_ALTA` vanno risolti nel grafo (e.g. tarando meglio il `guardrail_gate` o abbassando a MEDIA se non si è sicuri della citazione). Non ho toccato il grafo come da disposizioni.
