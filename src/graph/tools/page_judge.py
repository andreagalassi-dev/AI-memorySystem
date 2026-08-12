"""Verificatore di pertinenza della pagina nel ramo VISUAL — giudice TESTUALE.

Cosa fa
-------
Prende la domanda dell'utente e i CARTIGLI (`src/retrieval/page_title.py`) delle
pagine che il ramo visivo sta per citare, li etichetta A/B/C e chiede a un
modello **testuale** quale risponde, ammettendo esplicitamente **NESSUNA**.

Perche' testuale e non visivo
-----------------------------
Il VLM e' cieco (sonde 12/08: fabbrica cartigli, elenca codici inesistenti, dice
SI' a qualunque verifica). Il modello TESTUALE a scelta chiusa invece funziona:
5/5 sulle sonde, distingue "rilascio frizione" (FIG S040 CLUTCH RELEASE) da
"gruppo frizione completo" (FIG S038 CLUTCH ASSY) e rifiuta cross-lingua
("Explosionszeichnung der NordCap Kuehltruhe" su cartigli Branson -> NESSUNA).

Contratto: puo' SOLO aggiungere rifiuti
---------------------------------------
  - `REFUSE` (= NESSUNA) -> il nodo visivo degrada a BASSA ("non lo so"),
    citazioni vuote. E' l'UNICO motivo di rifiuto;
  - `CONFIRM`, qualunque sia la pagina indicata -> si risponde come prima. Se la
    pagina indicata non e' la prima, resta una riga di audit con `OTHER_PAGE_TAG`
    (vedi sotto: e' diagnostica, non un rifiuto);
  - `SKIP`    -> non blocca. E' il degrado PERMISSIVO sull'infrastruttura:
    cartiglio assente, LLM non cablato, errore o timeout del modello. Un guasto
    non deve fabbricare rifiuti.

Fail-closed dove conta: sul PARSING. Una risposta fuori dall'insieme chiuso
(lettera inesistente, testo libero, due lettere) vale NESSUNA, mai conferma.

Perche' ALTRA_PAGINA NON rifiuta (non ripristinare la "regola stretta")
-----------------------------------------------------------------------
Una versione precedente rifiutava anche quando il giudice indicava una pagina
diversa dalla prima. Sembrava un irrigidimento della sicurezza. **Era una perdita
netta, e il golden gate l'ha smascherata**: 3 query cambiate, tutte in peggio —
`V004`, `V011`, `V015` da `PASS/MEDIA` a `MISS/BASSA`, distribuzione da
`PASS 10 / MISS 10` a `PASS 7 / MISS 13`, zero guadagni.

Il difetto e' di ragionamento, non di taratura, e nessuna soglia lo aggiusta:

  1. il ramo visivo mostra all'utente fino a `VISUAL_K = 3` pagine, e il metro
     del progetto (`citation_ok` del gate) conta come corretta una risposta in
     cui la pagina attesa e' fra **quelle citate**, non solo in cima;
  2. il giudice sceglie **fra le candidate che gli passiamo**, cioe' esattamente
     quelle tre. Quindi `ALTRA_PAGINA` significa, alla lettera: "la pagina giusta
     e' una di quelle che stiamo gia' mostrando, solo non e' la prima".

L'utente la vede comunque. Rifiutare li' non elimina un errore: **butta via una
risposta che conteneva la risposta**. Nei tre casi `ALTRA_PAGINA` del golden la
pagina indicata dal giudice era esattamente il gold ed era gia' a schermo
(`V004` -> gehl p.94, `V015` -> kohler_marine p.14, `CV10` -> bell p.19).

`NESSUNA` resta invece un rifiuto legittimo: dice che **nessuna** delle pagine
mostrate va bene, e li' "non lo so" e' l'unica risposta onesta.

La riga `OTHER_PAGE_TAG` resta perche' misura una cosa reale e diversa — quante
volte il primo posto e' sbagliato pur avendo la risposta in lista — ed e' il dato
su cui si deciderebbe un eventuale RIORDINO, che e' il vero rimedio a quel caso.

Limite noto: le COPERTINE dei cataloghi ricambi
-----------------------------------------------
Il cartiglio di una copertina (`'CATALOGO PEZZI DI RICAMBIO | SPARE PARTS
CATALOGUE | ERSATZTEILELISTE'`) risponde, *letteralmente*, a qualunque domanda
generica di ricambi su quella macchina — e il giudice non ha modo di sapere che
dietro c'e' un frontespizio invece di una tavola. Due casi reali del truth set
sono la stessa patologia e il giudice ne prende solo uno:
  - `V011` "esploso della trinciatrice Benassi T623" -> il sistema mostrava la
    COPERTINA `benassi_t623_flail_mower` p1 (score 1.4375, il secondo piu' alto
    dell'intero set) invece della tavola a p2. Il giudice rifiuta: PRESO;
  - `V014` "tavola ricambi della macchina per cucire Rimoldi 327" -> copertina
    `rimoldi_327_spareparts` p1 (score 1.1875, il piu' alto dell'intero set)
    invece della tavola a p7. Il giudice CONFERMA: NON preso, ed e' il
    confident-wrong peggiore del set.
La differenza fra i due e' fortuita, non strutturale: entrambe le copertine
"rispondono" alla domanda quanto la tavola. Serve un segnale in piu' (vedi il
report: una copertina non nomina un GRUPPO specifico, una tavola si'). NON
implementato, e nessuna soglia sullo score puo' sostituirlo: gli score di queste
due copertine sono i due piu' alti misurati.
"""

from __future__ import annotations

import logging
import re
from typing import Callable, NamedTuple, Sequence

from src.retrieval.page_title import extract_page_title

logger = logging.getLogger(__name__)

# Il ramo visivo cita al massimo VISUAL_K=3 pagine: l'insieme chiuso e' A/B/C.
MAX_JUDGE_PAGES = 3
LETTERS = "ABC"
NO_TITLE = "(testo della pagina non disponibile)"

# Marcatori grep-abili, per non dover interpretare prosa a valle. Uno solo dei due
# e' un motivo di RIFIUTO: l'altro e' pura diagnostica (vedi docstring, sezione
# "Perche' ALTRA_PAGINA non rifiuta").
REFUSE_NONE_TAG = "motivo=NESSUNA"      # nessuna pagina mostrata va bene -> RIFIUTO
OTHER_PAGE_TAG = "nota=ALTRA_PAGINA"    # una va bene ma non e' la prima -> si risponde

# Prompt MISURATO. Una prima versione chiudeva con "nel dubbio rispondi NESSUNA":
# su 21 risposte visive del truth set rifiutava 4 pagine CORRETTE per eliminare 2
# sbagliate (netto -2). Il "nel dubbio" non e' prudenza, e' un pregiudizio: il
# cartiglio e' un'INTESTAZIONE, non il contenuto della pagina, e pretendere che
# contenga tutte le parole della domanda fabbrica rifiuti. La versione qui sotto,
# che ancora il rifiuto all'OGGETTO della domanda, misura 1 rifiuto sbagliato
# contro 1 confident-wrong eliminato (vedi il report del ciclo).
SYSTEM_PAGE_JUDGE = (
    "Sei un verificatore di pertinenza in un sistema di documentazione tecnica. "
    "Ricevi una domanda e le intestazioni (cartigli) di alcune pagine di manuale. "
    "Devi dire QUALE pagina risponde alla domanda, scegliendo UNA sola lettera "
    "fra quelle proposte, oppure NESSUNA se nessuna delle pagine e' pertinente.\n"
    "Regole:\n"
    "- rispondi SOLO con la lettera (es. A) oppure con la parola NESSUNA;\n"
    "- nessuna spiegazione, nessuna frase, nessuna punteggiatura;\n"
    "- il cartiglio e' solo l'intestazione: se riguarda l'oggetto della domanda "
    "(stessa macchina, stesso gruppo, stesso componente) la pagina e' pertinente, "
    "anche se non contiene tutte le parole della domanda;\n"
    "- rispondi NESSUNA solo se NESSUNA delle pagine riguarda l'oggetto della "
    "domanda: per esempio parlano di un'altra macchina o di un altro costruttore."
)


class JudgeOutcome(NamedTuple):
    """Esito del verificatore. `decision` in {CONFIRM, REFUSE, SKIP}."""
    decision: str
    chosen_index: int | None
    reason: str


def build_judge_prompt(query: str, titles: Sequence[str | None]) -> str:
    """Prompt a scelta chiusa: cartigli etichettati + NESSUNA sempre ammessa."""
    lines = [f"Domanda dell'utente: {query}", "", "Pagine candidate:"]
    for letter, title in zip(LETTERS, titles):
        lines.append(f"{letter}) {title if title else NO_TITLE}")
    lines += [
        "",
        f"Quale pagina risponde alla domanda? Rispondi con "
        f"{', '.join(LETTERS[:len(titles)])} oppure NESSUNA.",
    ]
    return "\n".join(lines)


_NO_RE = re.compile(r"\b(nessuna|none|keine|nothing)\b", re.IGNORECASE)
_ISOLATED_LETTER_RE = re.compile(r"(?<![A-Za-z])([A-Za-z])(?![A-Za-z])")


def parse_judge_verdict(raw: str, n_candidates: int) -> int | None:
    """Indice della pagina scelta, oppure `None` = NESSUNA.

    Fail-closed sul parsing: qualunque cosa non sia ESATTAMENTE una delle lettere
    ammesse (testo libero, lettera fuori insieme, due lettere) vale NESSUNA.
    """
    if not raw or not raw.strip():
        return None
    if _NO_RE.search(raw):
        return None
    allowed = LETTERS[:max(0, min(n_candidates, len(LETTERS)))]
    found = {m.group(1).upper() for m in _ISOLATED_LETTER_RE.finditer(raw)
             if m.group(1).upper() in allowed}
    if len(found) != 1:
        return None                      # zero o ambiguo -> NESSUNA
    return allowed.index(found.pop())


def make_page_judge(
    llm,
    *,
    get_title: Callable[[str, int], str | None] = extract_page_title,
) -> Callable[[str, Sequence], JudgeOutcome]:
    """Binder: `(query, pages) -> JudgeOutcome`. Da iniettare in `Deps.page_judge`."""

    def _judge(query: str, pages: Sequence) -> JudgeOutcome:
        if not pages:
            return JudgeOutcome("SKIP", None, "nessuna pagina candidata")
        if llm is None:
            return JudgeOutcome("SKIP", None, "LLM non cablato")

        candidates = list(pages[:MAX_JUDGE_PAGES])
        titles = [get_title(p.doc, p.page) for p in candidates]

        # Se la pagina TOP non ha cartiglio non abbiamo evidenza su cio' che il
        # sistema sta per mostrare: rifiutare qui sarebbe un rifiuto basato
        # sull'assenza di dati, non sui dati. Permissivo, e non spendiamo l'LLM.
        if not titles[0]:
            return JudgeOutcome("SKIP", None,
                                f"cartiglio assente per la pagina top "
                                f"({candidates[0].doc} p.{candidates[0].page})")

        prompt = build_judge_prompt(query, titles)
        try:
            raw = llm.generate(SYSTEM_PAGE_JUDGE, prompt)
        except Exception as exc:
            logger.warning("giudice pagina: LLM non disponibile (%s: %s) -> nessun "
                           "blocco", type(exc).__name__, exc)
            return JudgeOutcome("SKIP", None, f"errore LLM: {exc}")

        idx = parse_judge_verdict(raw or "", len(candidates))
        verbatim = " ".join((raw or "").split())[:80]
        if idx is None:
            return JudgeOutcome("REFUSE", None, f"NESSUNA (risposta: {verbatim!r})")
        return JudgeOutcome("CONFIRM", idx, f"{LETTERS[idx]} (risposta: {verbatim!r})")

    return _judge
