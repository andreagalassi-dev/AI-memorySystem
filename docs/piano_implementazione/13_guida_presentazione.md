# Guida alla presentazione — Demo di mercoledì 15/07

> **Per chi:** il presentatore (tu). Contiene TUTTO quello che devi fare prima e durante
> la demo: preparazione, script, imprevisti, domande prevedibili. Lo script tecnico
> dettagliato delle query è in `11_script_demo.md` (nel worktree demo); questa guida lo
> ingloba e aggiunge il resto.

---

## 1. Cosa stai presentando (il messaggio in 30 secondi)

Un **motore di conoscenza per il manifatturiero** che risponde su manuali tecnici,
tabelle ricambi ed esplosi in 3 lingue (IT/EN/DE), con un principio non negoziabile:

> **"Giusto, oppure 'non lo so' — mai sbagliato in silenzio."**

I tre punti di forza da vendere esplicitamente:
1. **Zero allucinazioni sui codici**: un guardrail deterministico (non-AI) impedisce di
   mostrare codici che non esistono nei documenti. Ogni risposta cita documento e pagina.
2. **Ricerca visiva**: trova la tavola esplosa giusta dentro migliaia di pagine,
   partendo da una domanda in linguaggio naturale.
3. **Onestà dichiarata**: quando non ha evidenze, lo dice. Il rifiuto è una feature,
   non un difetto — nel manifatturiero un ricambio sbagliato costa più di una risposta
   mancata.

---

## 2. MARTEDÌ (il giorno prima) — prova generale

- [ ] Avvia lo stack (sezione 3, stessi comandi) e fai il giro completo delle 7 query
      dello script, in ordine, una volta.
- [ ] Cronometra ogni query (da Invio a risposta completa) e annota i tempi.
- [ ] **Screenshot di OGNI risposta riuscita** (Cmd+Shift+4) salvato in
      `../AI-memorySystem-demo/output/demo_assets/` con nomi `piano_b_...`.
- [ ] **Screen recording** (Cmd+Shift+5) di almeno un giro visivo completo
      (query Gehl → immagine esploso a schermo).
- [ ] Passa i tempi all'agente per l'ultimo commit ammesso, oppure aggiorna tu
      `11_script_demo.md` e committa nel worktree: `git commit -am "D-3: tempi reali prova generale"`.
- [ ] Se un flusso si rompe: NON fixare da solo, riporta al reviewer con l'errore esatto.
- [ ] A fine prova: lascia i container docker attivi (girano da giorni senza problemi).

---

## 3. MERCOLEDÌ MATTINA — preparazione (30-40 min prima, con calma)

**FREEZE totale: da stamattina nessun commit, nessun update, nessun download. Solo avviare e provare.**

### 3.1 Avvio servizi (ordine esatto)

```bash
# 1. Servizi core (probabilmente già su — verifica soltanto)
cd ~/Documents/AI/AI-memorySystem
docker ps          # devono esserci: aike_postgres (healthy) e aike_qdrant (Up)
# se spenti: docker compose up -d qdrant postgres

# 2. Ollama: apri l'app Ollama (icona in menu bar), poi verifica
ollama list        # deve comparire qwen2.5:14b (e mxbai-embed-large se elencato nello script)

# 3. UI demo (QUESTO TERMINALE RESTA APERTO per tutta la demo)
cd ~/Documents/AI/AI-memorySystem-demo
source ../AI-memorySystem/.venv/bin/activate
streamlit run src/ui/app.py --server.port 8501
```

Apri `http://localhost:8501` nel browser.

### 3.2 Riscaldamento (fondamentale)

- [ ] Lancia una query codice qualsiasi (es. `codice 877615`) → scalda Ollama
      (il primo caricamento del 14B richiede decine di secondi).
- [ ] Lancia TUTTE le query visive dello script (Gehl, poi Micron) UNA volta:
      la prima impiega **2-5 minuti** (carica i modelli + reranking), le successive
      ~1,5 minuti. I risultati finiscono in cache: in demo saranno ISTANTANEI.
      **Aspetta ogni risposta senza cliccare altro.**
- [ ] Verifica che l'immagine dell'esploso compaia davvero a schermo.
- [ ] ⚠️ Da qui in poi NON riavviare streamlit (la cache vive nel processo):
      se il terminale muore, rifai il riscaldamento visivo da capo.

### 3.3 Igiene macchina

- [ ] Chiudi le app pesanti non necessarie (RAM: il Mac ha 32GB e li usa quasi tutti).
- [ ] Disattiva le notifiche (Impostazioni → Focus/Non disturbare).
- [ ] Tieni aperta in un tab la cartella `output/demo_assets/` (Piano B a un click).
- [ ] Batteria carica / alimentatore attaccato. Niente sospensione automatica.

---

## 4. LO SVOLGIMENTO — le 7 query, in quest'ordine

> Regola d'oro: **una query alla volta, aspetta sempre la risposta completa prima di
> digitare la successiva.** Non usare query fuori script: quelle scelte sono collaudate.

| # | Query da digitare | Cosa dire mentre gira | Cosa deve apparire |
|---|---|---|---|
| 1 | `Qual è la capitale della Francia?` | "Prima verifichiamo che NON allucini: gli chiedo una cosa fuori dai manuali." | Rifiuto: "Non ho evidenza sufficiente" → **venderlo**: "non inventa mai" |
| 2 | `codice 877615` | "Ora un codice reale dai nostri manuali." | EXTRACTOR ASSY. con fonte (rotax_912iS_IPC, pag. 15), fiducia ALTA |
| 3 | `codice PD16T10-40AL-F` | "Anche codici complessi con formattazioni difficili." | Codice puleggia con fonte (pi_pulley_listino_pulegge_2025, pag. 29), fiducia ALTA |
| 4 | `exploded view of the Gehl foot valve` | "A volte serve lo spaccato tecnico: chiediamo l'esploso di una valvola." | Immagine esploso da gehl_v270_parts pag. 167 (pre-riscaldata: istantanea dalla cache) |
| 5 | `exploded view of the Kids MICRON sweeper` | "Stessa cosa su un manuale completamente diverso." | Immagine esploso da flow_exploded_2022 pag. 16 |
| 6 | `chiusura di scarico con la retina per i capelli, diametro 72` | "L'utente non sa il termine tecnico: descrive. Il sistema capisce che è un 'tappo con filtro'." | Riferimento al tappo con filtro diametro 72 |
| 7 (opz.) | `quale norma indica il montaggio B3 o B5 di un motore elettrico?` | Solo se c'è tempo e tutto è filato liscio. | Riferimento alla normativa IEC 34-7 |

Suggerimenti di conduzione:
- Mentre una query gira, **parla tu**: riempi l'attesa con lo speaker note, mai silenzio
  davanti allo spinner.
- Dopo ogni risposta, **indica col cursore la citazione** (documento, pagina) e il badge
  di fiducia: è il differenziatore rispetto a un chatbot generico.
- La query 1 va fatta PER PRIMA: incornicia tutto il resto ("questo sistema prima di
  tutto non mente").

---

## 5. GESTIONE IMPREVISTI — Piano B

| Sintomo | Azione immediata |
|---|---|
| Una query dà risposta strana/errore | "Vi mostro il flusso registrato" → apri lo screenshot corrispondente da `output/demo_assets/` e prosegui con la query successiva live |
| La UI si blocca / spinner infinito | Ricarica la pagina del browser (F5). Se non basta: Ctrl+C nel terminale streamlit e rilancia il comando (≈20s). Intanto mostra uno screenshot |
| La query visiva impiega troppo | È il cold start: parla del funzionamento ("sta analizzando migliaia di pagine come immagini"). Se supera il minuto abbondante → screenshot |
| Crash totale / macchina inutilizzabile | Presenta l'intero giro con screenshot + screen recording: "vi mostro la sessione registrata ieri sul sistema reale" |

**Mai** dire "non funziona": si passa al materiale registrato con naturalezza, è stato
preparato apposta.

---

## 6. DOMANDE PREVEDIBILI (e risposte oneste)

- **"E i prezzi dei ricambi?"** → "Il lookup prezzi è in sviluppo: l'estrazione
  strutturata dai listini è attiva ma non ha ancora superato il nostro standard di
  accuratezza carattere-per-carattere, quindi per il nostro principio non la mostriamo
  finché non è perfetta." (Verità: T2 — estrazione non verbatim, esclusa dalla demo.)
- **"Funziona in tedesco?"** → "Il corpus è IT/EN/DE e le metriche le misuriamo per
  lingua; il retrieval multilingua è attivo, l'ottimizzazione cross-lingua è nella
  roadmap." (Verità: tallone noto cross-lingua IT sul visivo.)
- **"Che infrastruttura serve?"** → "Tutto quello che vedete gira in locale su questo
  laptop: nessun cloud, i vostri manuali non escono dall'azienda."
- **"Quanto è preciso?"** → "Sulle query con codice la precisione con fiducia ALTA è
  del 100% sul nostro set di valutazione: se risponde ALTA, è giusto. Quando non è
  sicuro, lo dichiara."
- **"Si integra con l'ERP?"** → "È in roadmap; oggi lavora sui documenti."
- **Se chiedono una query libera dal pubblico** → accettala SOLO su temi da manuale
  tecnico e presentala così: "proviamo — e se non ha l'evidenza ce lo dirà, che è
  esattamente il comportamento che vogliamo." Il rifiuto onesto è comunque una demo
  riuscita.

---

## 7. COSA NON FARE (mercoledì)

- ❌ Nessun `git commit`, `git pull`, update di librerie o modelli. FREEZE totale.
- ❌ Non chiudere il terminale con streamlit (la UI muore con lui).
- ❌ Non lanciare due query in parallelo, soprattutto visive (RAM).
- ❌ Non improvvisare query fuori script se non nel modo protetto del punto 6.
- ❌ Non aprire applicazioni pesanti durante la demo.

In bocca al lupo: il sistema c'è, è collaudato, e la sua onestà è il tuo miglior
argomento di vendita.
