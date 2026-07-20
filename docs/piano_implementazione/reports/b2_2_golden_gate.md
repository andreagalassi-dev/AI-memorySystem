# Report Golden Gate B2.2 (Rework)

## Metriche per lingua
- **IT**: 23/30 (76.7%)
- **EN**: 4/6 (66.7%)
- **DE**: 3/4 (75.0%)
- **ES**: 1/1 (100.0%)

## Esiti Hard Floor
Dopo aver corretto il bug nello script di valutazione (la funzione `has_citation` ora riconosce correttamente il formato `[doc, page]` restituito dal grafo in produzione), la quasi totalità dei falsi negativi `WRONG_ALTA` è sparita.
La stragrande maggioranza delle query `code` ora restituisce correttamente `PASS_ALTA` (o `MISS` con livello corretto).

Tuttavia, sopravvivono **2 violazioni REALI del floor duro WRONG_ALTA**:
- `C_DE_02`: WRONG_ALTA (sbagliato doc/page ma livello ALTA)
- `C_EN_02`: WRONG_ALTA (sbagliato doc/page ma livello ALTA)

Questo indica che il modello è stato troppo confidente pur recuperando o citando una pagina/documento non esatti per queste due query. Non ci sono state violazioni del floor `RBAC_LEAK` o `REFUSAL`.

## Baseline
La baseline (`golden_baseline_v1.json`) è stata rigenerata partendo da uno stato pulito. L'audit JSON ora contiene anche un campo `real_citations` per ispezionare l'esatta forma delle citazioni.
Il job CI fallirà fino a che le due violazioni `WRONG_ALTA` in DE ed EN non saranno corrette nel grafo (il reviewer è avvisato).
