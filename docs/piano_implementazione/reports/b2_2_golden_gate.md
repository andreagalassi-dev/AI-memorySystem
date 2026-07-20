# Report Golden Gate B2.2 (Rework v3)

## Metriche per lingua
- **IT**: 23/30 (76.7%)
- **EN**: 5/6 (83.3%)
- **DE**: 4/4 (100.0%)
- **ES**: 1/1 (100.0%)

## Esiti Hard Floor
L'Harness del Golden Gate è stato ulteriormente raffinato per risolvere il problema dei falsi positivi nel match delle citazioni per i codici. Adesso, la valutazione di answerability per le query di `code` è "index-aware". Se il LLM cita una pagina diversa da quella marcata in dataset ma **la pagina citata contiene effettivamente il codice** (verificato tramite l'indice Qdrant / PostgreSQL `aike_chunks` e `righe_listino`), la risposta viene giudicata come un successo (`PASS_ALTA`) anziché una violazione (`WRONG_ALTA`).

Con questo fix, le **violazioni del floor duro sono esattamente 0**.
I falsi `WRONG_ALTA` residui (`C_DE_02` e `C_EN_02`) sono stati corretti. Non si registrano leak RBAC né fallimenti nei refusal.

## Ricorrenza codici (Falsi Positivi pre-v3)
La necessità di adottare un meccanismo di *index-aware code verification* al posto di un semplice match esatto `[doc, page] == [expected_doc, expected_page]` è emersa dall'analisi di 2 query specifiche:
- **C_DE_02 (codice 70167)**: Il dataset si aspettava il documento `kbs_gastrotechnik_1717` pagina 17. Il sistema rispondeva in modo sicuro (ALTA) citando `kbs_gastrotechnik_20322006` pagina 14. Un controllo sul DB ha confermato che il pezzo "ABLAUFGARNITUR" col codice `70167` esiste regolarmente in *entrambi* i cataloghi KBS in quelle esatte pagine.
- **C_EN_02 (codice 22112)**: Il dataset puntava a `crusader_marine_L510008` pagina 55. Il modello citava la pagina 53 dello *stesso documento*. Di nuovo, il catalogo riporta lo stesso ricambio ("Clamp, oil line retaining") ripetuto su più pagine, inclusa la 53.

Entrambe erano risposte valide e pertinenti, pur deviando dal `gold` annotato. Ora l'harness verifica che il codice sia fisicamente presente nel pezzo di indice citato, garantendo un'applicazione solida del floor per le allucinazioni ma tollerando la naturale ridondanza documentale.

## Baseline
La baseline (`golden_baseline_v1.json`) è stata ripulita e rigenerata da zero, ed è stabile e verde, pronta a bloccare future regressioni (ora con 0 `WRONG_ALTA`).
L'audit cattura ora correttamente anche l'eventuale mismatch tra pagina citata e pagina attesa usando il booleano `matched_expected_page`, pur non usandolo per sanzionare il modello.
