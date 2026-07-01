# Progetto: AI Knowledge Engine per il Manifatturiero Avanzato
## Architettura Tecnica + Roadmap Eseguibile (PoC → SaaS)
### Revisione 4 — riorganizzata come piano di lavoro punto-per-punto

> **Nota di revisione (rev. 4):** il contenuto tecnico delle revisioni precedenti è **invariato e completo**. Cambia solo l'organizzazione: il documento è stato trasformato da specifica *tematica* a **roadmap eseguibile**. Le decisioni e le specifiche restano nel **Riferimento Tecnico** (ex Sezioni 3–8, in fondo); davanti c'è ora una **Roadmap (P0–P7)** in cui ogni punto è un traguardo a sé, con sotto-task spuntabili, un criterio di "fatto" e un punto di pausa. Si lavora dall'alto verso il basso. I tag delle revisioni precedenti (`[DA DEFINIRE]`, `[NUOVO]`, `[rev. 3]`) sono conservati.

---

## Come usare questo documento

Si esegue la **Roadmap** dall'alto verso il basso, un punto alla volta.

**Legenda:**
- 🎯 **Obiettivo** — cosa deve esistere alla fine del punto.
- ☐ **Sotto-task** — i passi concreti (da spuntare).
- ✅ **Definition of Done (DoD)** — la condizione oggettiva che dice "questo punto è chiuso, ci si può fermare".
- 🔗 **Dipende da** — quali punti devono essere chiusi prima.
- 📖 **Riferimento** — la sezione tecnica in fondo con il dettaglio del "perché/come".
- ⏸ **PAUSA** — punto naturale dove fermarsi.
- ⚠️ **GATE** — punto di decisione go/no-go: se fallisce, ci si ferma e si risolve **qui**, non si prosegue.

**Principio guida (vale per ogni punto):** in questo dominio una risposta sbagliata ha un costo reale (un ricambio errato ferma una macchina). Il sistema deve essere **giusto, oppure dichiarare "non lo so" — mai sbagliato in silenzio.** Questo orienta ogni scelta.

---

# PARTE 1 — ROADMAP ESECUTIVA

## P0 — Fondamenta & corpus reale
🎯 **Obiettivo:** avere un ambiente locale che gira e il corpus *vero* (e peggiore) di un cliente sul disco.

☐ Setup repo + `docker-compose` con lo scheletro dei servizi: backend, vector DB, OCR engine, UI.
☐ Installare **Ollama** o **LM Studio**; verificare che l'inferenza locale risponda con un modello testo qualsiasi.
☐ Avviare **Qdrant** in container e verificare la connessione dal backend.
☐ Procurarsi da **UN** cliente reale il corpus vero — in particolare **le tabelle ricambi e gli esplosi peggiori** + qualche manuale **multilingua**.
☐ Catalogare formati presenti (PDF nativi/scansionati, DOCX, Excel, CAD…) e lingue.

✅ **DoD:** i container salgono, l'inferenza locale risponde, il corpus reale è sul disco e catalogato per formato/lingua.
🔗 **Dipende da:** nessuno — è il punto di partenza.
📖 **Riferimento:** §A (Architettura di base), §E.1 (Sorgenti dati).
⏸ **PAUSA consigliata.**

---

## P1 — Validazione ingestion ⚠️ GATE CRITICO
🎯 **Obiettivo:** scoprire **se** OCR ed estrazione tabelle reggono sul corpus vero, **prima** di costruire qualsiasi altra cosa. È il punto dove questi progetti tipicamente falliscono.

☐ Pipeline **OCR → Markdown** su un campione (Marker/Surya/MinerU, oppure GOT-OCR2/DeepSeek-OCR).
☐ Valutare la **qualità OCR** sui casi difficili: esplosi con linee di richiamo, layout multi-colonna, testo ruotato, multilingua.
☐ **Estrazione tabelle** in file strutturati separati (CSV/XLSX/JSON) con puntatore al documento d'origine.
☐ **TEST DEDICATO tabelle** (≠ test OCR): su un campione di tabelle reali "difficili", verificare che ogni `codice → descrizione → quantità` corrisponda all'originale. Controllare i modi di fallimento silenziosi: **celle unite**, **tabelle su più pagine**, **intestazioni ripetute**, **righe-totale/note scambiate per dati**.
☐ **Decisione GATE** documentata: l'ingestion regge? Se no, ci si ferma e si risolve qui.

✅ **DoD:** hai **due numeri** sul corpus reale — qualità OCR e accuratezza estrazione tabelle — e una **decisione go/no-go scritta**.
🔗 **Dipende da:** P0.
📖 **Riferimento:** §D (Pipeline ingestion), nota fallimenti tabelle, nota qualità OCR.
⏸ **PAUSA OBBLIGATORIA — è il gate del progetto.** (Copre i punti aperti #1 e #10.)

---

## P2 — Retrieval ibrido + misurazione
🎯 **Obiettivo:** trovare il documento/pagina giusto e **misurare** quanto bene lo fa.

☐ Indicizzazione testuale dei Markdown in Qdrant (chunking pulito).
☐ **Filtro pagine** in ingestion: classificatore (VLM piccolo/regole) etichetta "grafiche" vs "testuali".
☐ **Embedding visivi** (architetture tipo *ColPali*) **solo** sulle pagine grafiche (controlla storage/costo).
☐ **Ricerca ibrida**: semantica (vettoriale) **+** lessicale (BM25) — indispensabile per i codici alfanumerici tipo `AB-12345-X`.
☐ **Reranking** dei risultati prima di passarli all'LLM.
☐ Costruire il **set di valutazione**: ~50–100 coppie `domanda → risposta corretta` da un tecnico esperto (poche ore, non una persona full-time).
☐ Misurare **recall@k** e **correttezza data la giusta evidenza**, **separatamente per lingua**.

✅ **DoD:** recall@k e correttezza misurati sul corpus reale, scomposti per lingua. **Questi due numeri sono il primo caso ROI vendibile.**
🔗 **Dipende da:** P1 (gate superato).
📖 **Riferimento:** §D (Retrieval), §F.1 (Valutazione).
⏸ **PAUSA.** (Copre punti aperti #8 — calibrazione soglie — e #12 — metriche per lingua.)

---

## P3 — Orchestrazione a grafo + guardrail + incertezza
🎯 **Obiettivo:** trasformare il retrieval grezzo in risposte **affidabili**, con il guardrail sui codici e il percorso "non lo so".

☐ **LangGraph** (o LlamaIndex Workflows): nodi router → pipeline (OCR/multimodale/ibrida) → validazione → sintesi.
☐ **Router decisore** (VLM piccolo/classificatore + regole esplicite).
☐ Scegliere e collegare i **DUE modelli distinti**: ragionatore (solo testo) + VLM (visione). Scelta dopo test sul corpus.
☐ **Fusion**: Markdown pulito + tabella strutturata + eventuale immagine pagina → ragionatore per la sintesi.
☐ **Guardrail deterministico sui codici**: ogni codice citato nella risposta deve **esistere letteralmente** nella tabella recuperata; altrimenti la risposta viene **bloccata/degradata** a "verifica manuale".
☐ **Percorso "non lo so"**: livelli di sicurezza basati su **segnali oggettivi** (similarità retrieval, accordo testo/visione, citazione presente) — **non** sull'auto-confidenza dell'LLM.
☐ Misurare il comportamento "non lo so": distinguere **silenzio giusto** da **silenzio per pigrizia del retrieval**.

✅ **DoD:** la chat risponde con fonte+pagina, **blocca i codici allucinati**, e sa dichiarare "non lo so" sui segnali deboli.
🔗 **Dipende da:** P2.
📖 **Riferimento:** §C (Motore a grafo), §D (Validazione deterministica), §F.2 (Gestione incertezza).
⏸ **PAUSA.** (Copre punti aperti #2, #11.)

---

## P4 — Gestione dati, versioning e RBAC
🎯 **Obiettivo:** dati gestibili nel tempo e accessi filtrati per ruolo.

☐ **DB di supporto** (PostgreSQL/MongoDB) per metadati, inventario simulato e **registro versioni**.
☐ **Versioning documenti**: stato *attuale*/*sostituito*; l'AI consulta prima le versioni attuali; se il dato viene **solo** da un file sostituito, risponde **avvisando** che è obsoleto. Gli embedding vecchi si **marcano**, non si cancellano.
☐ **Onboarding (Fase 1):** upload manuale; usare i **nomi delle cartelle** come *indizio* (non regola) per classificare i file.
☐ **RBAC (ruoli):** ogni query porta il ruolo → **filtro obbligatorio sui metadati Qdrant** (commerciale / tecnico / amministrazione). Un commerciale non recupera i documenti tecnici.

✅ **DoD:** caricare una nuova versione marca la vecchia come sostituita; l'avviso "obsoleto" compare; un ruolo non vede i documenti di un altro ruolo.
🔗 **Dipende da:** P3.
📖 **Riferimento:** §E (Gestione dati & versioning), §F.3 (RBAC).
⏸ **PAUSA.** (Copre punti aperti #3, #4.)

---

## P5 — UI PoC + ciclo di feedback
🎯 **Obiettivo:** una demo con "wow effect" e una raccolta feedback **strutturata** che alimenta la qualità.

☐ UI **Streamlit** (o Gradio) focalizzata sulla chat.
☐ La risposta mostra: **(1)** testo ancorato + livello di sicurezza, **(2)** pagina esatta citata, **(3)** rendering visivo della pagina/esploso, **(4)** stock (simulato in Fase 1), **(5)** avviso versione obsoleta se applicabile, **(6)** azione **conferma / correggi / segnala**.
☐ Il feedback viene salvato in modo **strutturato** (query + contesto recuperato + fonti citate), non come testo libero.
☐ Le correzioni alimentano il set di valutazione, **con moderazione** prima di entrare nel ground truth (una correzione sbagliata non deve "avvelenare" il set).

✅ **DoD:** demo end-to-end mostrabile a un cliente; ogni interazione produce feedback strutturato e ricollegabile alla fonte.
🔗 **Dipende da:** P4 (o P3 per una demo minima).
📖 **Riferimento:** §G (UI), §F.4 (Ciclo di feedback).
⏸ **PAUSA — qui hai un PoC vendibile.** (Copre punto aperto #13.)

> **Da mettere in conto fin da ora:** Streamlit è ottimo per il PoC ma **non** è un frontend SaaS di produzione. Il refactor verso backend dedicato (FastAPI) + frontend separato è in P7, ma va previsto, non scoperto a metà.

---

## P6 — Decisioni strategiche & go-to-market (parallelo, non-codice)
🎯 **Obiettivo:** validare il **modello di business** mentre il sistema si costruisce. Le domande si pongono già da P0; le risposte arrivano sui primi clienti reali.

☐ **Cronometrare le ore reali di onboarding** sui primi 2–3 clienti → dice se sei **prodotto** (margini alti, scala col software) o **consulenza** (margini bassi, scala assumendo persone). È *la* metrica del modello.
☐ **Decisione deployment:** on-prem/appliance vs cloud SaaS vs **ibrido**. (La Fase 0 local-first è già il 90% di un'appliance.)
☐ **Mappare 2–3 concorrenti reali** nel mercato di riferimento (la difesa è la verticalità + integrazione locale, non il retrieval in astratto).
☐ **Modello di prezzo:** tier piatti/per-postazione vs a consumo; + **clausole di limitazione di responsabilità** (sistema = supporto alla decisione, non decisore).

✅ **DoD:** hai i numeri di onboarding dei primi clienti e una **decisione di deployment scritta**.
🔗 **Dipende da:** si avvia in parallelo; le metriche si raccolgono da P5 in poi.
📖 **Riferimento:** §H (Analisi strategica).
⏸ **PAUSA.** (Copre punti aperti #14, #15, #16, #17.)

---

## P7 — Fase 2: Cloud SaaS, multi-tenancy, integrazione ERP (futuro)
🎯 **Obiettivo:** scalare oltre il single-tenant locale, **solo dopo** aver dimostrato l'accuratezza.

☐ **Refactor frontend/backend:** da Streamlit a **FastAPI** + frontend separato (auth multi-ruolo/multi-tenant, sessioni concorrenti, billing).
☐ **Multi-tenancy isolata:** collezione-per-tenant + filtro dati a livello dati + **test automatici che provano a far trapelare dati** tra tenant.
☐ **Inferenza condivisa:** coda richieste + **batching** per abbattere il costo/query; modellare il **break-even** sul costo GPU idle.
☐ **Prima integrazione ERP:** non l'API live, ma una **sincronizzazione notturna in sola lettura** (codici + giacenze + prezzi → Postgres interno), su **UN** gestionale.
☐ **Data residency:** dati a riposo in UE; API esterne (se approvate dal cliente) → provider USA affidabili.

✅ **DoD:** un secondo tenant isolato e testato contro i leak; un ERP sincronizzato in sola lettura interrogabile dal grafo.
🔗 **Dipende da:** P5 + decisione di deployment in P6.
📖 **Riferimento:** §A (Cloud), §E (Integrazione live), §F.3 (Multi-tenancy), §H (economia SaaS).
⏸ **PAUSA.** (Copre punti aperti #5, #6, #7, #9.)

---

# PARTE 2 — RIFERIMENTO TECNICO

*(Contenuto invariato dalle revisioni precedenti — è il "perché/come" a cui rimandano i punti della roadmap.)*

## §A. Architettura di base e infrastruttura

Approccio interamente **containerizzato** per portabilità, deployment e isolamento dei dati.

**Strategia per fasi:**
* **Fase 0 (priorità attuale): tutto in locale**, single-tenant, per validare la qualità prima del cloud. Il salto al cloud solo dopo aver dimostrato l'accuratezza sul corpus reale.
* **Fase 2 (futuro): cloud privato e SaaS.**

**Stack:** Docker/Docker Compose (Backend, Vector DB, OCR, UI); inferenza LLM locale via **Ollama**/**LM Studio** (azzera i costi API in test, sfrutta Apple Silicon/NVIDIA RTX); migrazione a **AWS/Azure** con container orchestrati (ECS/EKS o AKS) e GPU dedicate in Fase 2.

> **Nota costi cloud [DA DEFINIRE]:** in cloud le GPU si pagano anche da ferme. Servirà inferenza **condivisa tra tenant** con coda + **batching** (più query insieme → costo/query molto più basso) anziché una GPU per cliente. Il break-even degli abbonamenti va modellato su questo prima del lancio SaaS.

**Vincoli modelli/API:** modelli **in locale** di default. API esterne ammesse **solo come ultima spiaggia e previa approvazione del cliente** (provider USA affidabili).

## §C. Motore AI e orchestrazione a grafo

Orchestrazione **basata su grafi** per affidabilità rispetto a un agente "black-box".

```
               [ Input della Query dell'Utente ]
                              |
                              v
                   [ Router Intelligente ]
                  /           |           \
                 v            v            v
       [ Pipeline OCR ]  [ Pipeline ]  [ Pipeline ]
         (Markdown)     (Multimodale)    (Ibrida)
                \            |            /
                 v           v          v
              [ Validazione e Sintesi Finale ]
                              |
                              v
                  [ Risposta con Fonti e UI ]
```

* **Framework:** **LangGraph** (o LlamaIndex Workflows). Ogni azione è un *nodo*, le transizioni sono *archi*.
* **Router decisore:** analizza la query e sceglie il percorso (codice preciso → testuale; schema/esploso → ispezione visiva). Piccolo VLM/classificatore + regole.

**Due modelli distinti, orchestrati dal grafo:**
1. **Ragionatore (solo testo):** sintetizza, ragiona sui codici, valida. *Esempi:* Qwen3, DeepSeek, Mistral, Llama, GLM.
2. **VLM (visione + testo):** "legge" le pagine renderizzate, interpreta esplosi e schemi. *Esempi:* Qwen2.5/3-VL, InternVL3, GLM-4.5V, Pixtral. Per la sola conversione PDF→Markdown: Marker/Surya/MinerU, GOT-OCR2/DeepSeek-OCR.

> I modelli sono **esempi**; la scelta definitiva è dopo i test sul corpus ed è un parametro sostituibile, non vincolante.

> **CORREZIONE — cosa significa "deterministico".** Il grafo rende deterministico il **percorso** (routing), **non la generazione**. Il nodo di sintesi resta un LLM che *può* allucinare. Ciò che riduce le allucinazioni è il **grounding** + la **citazione obbligatoria** + il **riscontro visivo**, non il grafo in sé. Da non vendere come "zero allucinazioni": è riduzione del rischio, non eliminazione.

## §D. Pipeline RAG ibrida (OCR + Vision)

**Ingestion — "trasforma e leggi una volta sola":** alla prima ingestione il documento si pre-elabora una volta e se ne salva una forma pronta, invece di reinterpretarlo a ogni query.

1. **OCR (Text-to-Markdown):** PDF → Markdown iper-strutturato; indicizzazione testuale leggera.
2. **Estrazione tabelle in file strutturati:** tabelle (ricambi, distinte) → CSV/XLSX/JSON separati, collegati al documento d'origine. L'AI interroga il dato pulito, preservando le relazioni riga-colonna.

> **Nota fallimenti estrazione tabelle [rev. 3]:** modi di fallimento **propri** e silenziosi: **celle unite**, **tabelle su più pagine** lette come due, **intestazioni ripetute**, **righe-totale/note scambiate per dati**. La tabella ricambi è il **cuore del valore**: va validata con un **test dedicato** (campione di tabelle difficili, verifica `codice→descrizione→quantità`), **insieme** alla validazione OCR.

3. **Multimodale con filtro in ingestion:** **non tutte** le pagine entrano nella pipeline visiva. Un classificatore etichetta "grafiche" vs "testuali"; **solo le grafiche** vengono renderizzate ad alta risoluzione e indicizzate con embedding visivi (tipo *ColPali*). Riduce drasticamente storage e calcolo.

> **Nota costi storage [DA DEFINIRE]:** gli embedding tipo ColPali usano molti vettori/pagina. Con corpus da 100–200 GB, filtrare le pagine grafiche è ciò che rende sostenibile l'approccio. Costo vector DB per cliente "medio"/"grande" da stimare.

**Retrieval:**
* **Ricerca ibrida [alta priorità]:** semantica + lessicale (BM25). I **codici alfanumerici** sono gestiti male dalla sola vettoriale; il lessicale è indispensabile. Qdrant la supporta.
* **Reranking:** riordina per pertinenza prima dell'LLM.
* **Ricerca visiva/esplosi:** il VLM lega frecce/leader lines ai codici.
* **Fusion:** Markdown + tabella strutturata + immagine pagina → ragionatore.

**Validazione deterministica — guardrail sui codici [rev. 3]:** un nodo **non-LLM** verifica che **ogni codice citato esista letteralmente** nella tabella recuperata. Codice allucinato → risposta **bloccata/degradata** a "verifica manuale". Controllo a costo quasi zero che chiude il rischio più caro; estendibile a quantità/prezzi/revisioni.

> **Nota qualità OCR [VALIDARE PER PRIMA COSA]:** OCR su esplosi con linee di richiamo, celle unite, multi-colonna e testo ruotato è il punto dove questi progetti falliscono. **Va validata sul corpus reale prima di sviluppare il resto.** Formati: PDF nativi, DOCX, Excel, CAD; corpus **multilingua**.

## §E. Gestione dati, versioning, integrazioni

**Fase 1 (PoC):** sorgenti = PDF/DOCX/Excel/CAD/immagini. **Qdrant**/ChromaDB con **collezione separata per tenant**. DB di supporto (Postgres/Mongo) per inventario simulato, metadati e registro versioni.

**Versioning documenti:** ogni documento è *attuale* o *sostituito*. Caricando una nuova versione, la precedente è marcata "sostituita" (embedding **marcati, non cancellati**). L'AI consulta **prima** le versioni attuali; se trova solo nel sostituito, risponde **avvisando** dell'obsolescenza. *[DA DEFINIRE]:* re-indicizzazione e conflitti.

**Onboarding:** Fase 1 = upload manuale; Fase 2 = cartella di rete/SharePoint/PLM con estrazione automatica. I nomi delle sottocartelle (`/Ricambi/`, `/Listini/`…) sono **indizio, non regola**.

**Integrazione live (Fase 2):** rinviata; l'architettura predispone solo gli innesti (stub). **Strategia prima integrazione:** non l'API live dell'ERP, ma una **sync in sola lettura** (job notturno copia codici + giacenze + prezzi → Postgres interno). Si interroga il DB sincronizzato, non l'ERP in tempo reale. *[DA DEFINIRE]:* quale gestionale per primo; se espone API o serve export.

## §F. Valutazione, incertezza, accessi, feedback

**§F.1 Valutazione qualità.** Due metriche **separate**: **recall@k** (il sistema ha *trovato* la pagina giusta tra i primi k?) e **correttezza** (dato il contesto giusto, la risposta è corretta?). Set di ground truth: ~50–100 coppie preparate **una volta** da un tecnico.

> **Precisazioni [rev. 3]:** il set è un **pavimento, non un tetto** (va fatto **crescere** dal feedback reale); **misurare anche il "non lo so"** (silenzio giusto vs pigrizia del retrieval); **validazione per lingua**, non in blocco (una lingua debole non deve nascondersi in una media).

**§F.2 Gestione incertezza.** Le "confidenze" dichiarate dall'LLM **non sono probabilità affidabili**. Usare **segnali oggettivi**: similarità del retrieval, accordo testo/visione, citazione concreta. Livelli (da calibrare): **alta** → risposta diretta con fonte; **media** → risposta con avviso "verifica consigliata"; **bassa** → **dichiara di non avere risposta affidabile**. Regola d'oro: **giusto oppure "non lo so", mai sbagliato in silenzio.**

**§F.3 RBAC vs Tenant.** *Ruoli (dentro l'azienda):* il ruolo nella query → filtro obbligatorio su Qdrant (`ruoli_consentiti`). *Multi-tenancy (tra aziende, Fase 2):* collezione separata per tenant + filtro **a livello dati** + **difesa in profondità** con test automatici di leak. *Data residency [DA DEFINIRE]:* dati a riposo in UE.

**§F.4 Ciclo di feedback [rev. 3].** Il feedback umano è **funzionalità di primo livello**: ogni risposta offre **conferma/correggi/segnala**, salvato in modo **strutturato**. Serve su tre piani: **qualità** (fa crescere il set), **fiducia** (argomento di vendita: "accuratezza misurata e in miglioramento"), **asset strategico** (corpus di interazioni non replicabile = parte del fossato). *[DA DEFINIRE]:* chi modera le correzioni prima del ground truth.

## §G. Interfaccia utente

**PoC:** Streamlit/Gradio, UI minimale focalizzata sulla chat. **Wow effect:** la risposta restituisce testo ancorato + sicurezza, pagina esatta, rendering visivo, stock, avviso obsoleto, feedback rapido.

> **Nota architetturale [IMPORTANTE]:** Streamlit **non** è un frontend SaaS multi-tenant di produzione. Per auth multi-ruolo/tenant, sessioni concorrenti, personalizzazione e billing servirà un backend dedicato (**FastAPI**) + frontend separato. Il refactor va in roadmap fin da ora (vedi P7).

## §H. Analisi strategica e modello di business [rev. 3]

Rischi **non tecnici**. La maggior parte di questi progetti non muore per l'architettura, ma per l'economia dell'onboarding e la confusione prodotto/consulenza.

* **8.1 "Stesso software per tutti" è una conclusione, non una premessa.** La parte difficile non è il software (componibile con open source) ma **il dato di ogni cliente** (PDF, codifiche, cartelle, lingue diverse). Nasconde un **costo di onboarding per-cliente**; se non industrializzato, trasforma il prodotto in consulenza. → *Misurare presto le ore reali di onboarding.*
* **8.2 Go-to-market:** un solo **lighthouse customer** in nicchia stretta → **caso ROI referenziabile** → templatizzare su 2–3 clienti simili **prima** di promettere flessibilità universale.
* **8.3 On-prem vs cloud:** "i dati restano da noi" è spesso **argomento di vendita**. Un'**appliance on-prem** aggira l'economia fragile delle GPU condivise. La Fase 0 local-first è già il 90% di un'appliance. → valutare un modello **ibrido**.
* **8.4 Il fossato** non è l'architettura (replicabile), ma: **ingestion messa a punto sui documenti manifatturieri**, **dataset di valutazione per verticale**, **integrazioni con gli ERP locali** (TeamSystem, Zucchetti, SAP Business One). Competitor di fondo: Copilot e RAG generaliste; difesa = verticalità + integrazione locale.
* **8.5 Prezzo e responsabilità:** il pricing a consumo genera ansia; **tier piatti/per-postazione** si vendono meglio. Sistema = **supporto alla decisione, non decisore**; difese tecniche **e** contrattuali.
* **8.6 La prima cosa concreta:** prendere **un** cliente, il **suo** corpus vero (tabelle ricambi + esplosi peggiori) e misurare **recall@k** e **correttezza** prima di scrivere altro codice. *(→ è esattamente P0–P2 di questa roadmap.)*

---

## Appendice — Checklist [DA DEFINIRE], rimappata per punto

| # | Punto aperto | Owner roadmap |
|---|---|---|
| 1 | Validazione qualità OCR sul corpus reale (multilingua) | **P1** |
| 10 | Test dedicato estrazione tabelle (celle unite, multi-pagina) | **P1** |
| 8 | Calibrazione soglie di sicurezza sui dati reali | **P2** |
| 12 | Comportamento "non lo so" + metriche per lingua | **P2 → P3** |
| 2 | Scelta definitiva dei due modelli (ragionatore + VLM) | **P3** |
| 11 | Guardrail deterministico sui codici nel nodo di validazione | **P3** |
| 3 | Re-indicizzazione e conflitti tra versioni | **P4** |
| 4 | Definizione fine dei permessi per ruolo (RBAC) | **P4** |
| 13 | Disegno del ciclo di feedback + moderazione correzioni | **P5** |
| 14 | Misura ore reali di onboarding (prodotto vs consulenza) | **P6** |
| 15 | Deployment: on-prem/appliance vs cloud vs ibrido | **P6** |
| 16 | Mappatura 2–3 concorrenti reali | **P6** |
| 17 | Modello di prezzo + clausole di responsabilità | **P6** |
| 5 | Stima costi GPU idle, storage vector DB, break-even | **P7** |
| 6 | Batching/coda per inferenza condivisa tra tenant | **P7** |
| 7 | Quale ERP integrare per primo (sync vs API live) | **P7** |
| 9 | Test di isolamento multi-tenant (leak tra aziende) | **P7** |
