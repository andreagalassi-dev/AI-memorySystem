"""Cartiglio di una pagina: il testo del PDF, non la sua immagine.

Perche' esiste
--------------
Il ramo visivo rispondeva `MEDIA` mostrando la pagina sbagliata senza mai dire
"non lo so". Nessuna soglia numerica lo risolve — e' misurato: su 34 query gli
score di pagine giuste e sbagliate sono sovrapposti e la risposta sbagliata
peggiore ha lo score PIU' ALTO dell'intero set. Serve un segnale diverso.

I due segnali che NON funzionano (sonde del reviewer, 12/08):
  - il VLM e' CIECO: interrogato sul cartiglio di tre pagine diverse ha
    fabbricato lo stesso titolo inventato, elencato codici inesistenti e detto
    SI' a qualunque domanda di verifica. Nessun percorso basato sulla visione;
  - l'embedding NON discrimina: BGE-M3 da' a `FRONT AXLE` 0.4221 su una query
    di controllo IRRILEVANTE, piu' di quanto dia alla corrispondenza corretta
    di una query pertinente. Nessuna soglia assoluta.

Il segnale che funziona e' il LIVELLO TESTO del PDF: sulle pagine-disegno il
cartiglio e' esatto e brevissimo (`'FIG S040 4520R CLUTCH RELEASE ... - 69 -'`,
55 caratteri). Va letto dal PDF e non dall'indice: le pagine-DISEGNO non hanno
chunk in `aike_chunks` (verificato: p69 e p22 di branson_4520R_parts, zero
chunk), solo le pagine-tabella ne hanno.

Due scelte dichiarate
---------------------
1. **Righe senza lettere = scartate.** Su un esploso il livello testo e'
   dominato dai numeri-pallino dei componenti (`'1 2 1 3 40 42 41 5 5 ...'`):
   presi come cartiglio sono rumore puro, e un giudice a valle non puo' che
   rifiutare. Teniamo solo le righe con almeno `MIN_LETTERS_PER_LINE` lettere.
   Se non ne resta nessuna -> `None` (nessun cartiglio), che a valle vale
   "non giudicare", non "rifiuta".
2. **Tetto a `PAGE_TITLE_MAX_CHARS` caratteri.** I cartigli veri stanno in
   50-80 caratteri; una pagina-TABELLA ne produce migliaia. Tronchiamo alla
   testa, dove sta l'intestazione. Il tetto e' MISURATO, non scelto a occhio:
   a 400 caratteri il verificatore rifiutava la pagina CORRETTA di `CV15`
   ("mounting angle kit with brackets for Abco valves") perche' la riga
   `'MAF-KIT-2 | Mounting angle kit (2 brackets + 2 bolts + 2 washers)'` sta
   all'offset 1465 del listino, oltre il taglio. A 1500 il caso rientra e nessun
   altro peggiora. Resta un limite noto: su una pagina-tabella molto lunga la
   riga pertinente puo' stare comunque oltre il tetto.

Degrado PERMISSIVO: PDF assente, pagina fuori range, livello testo vuoto o
qualunque errore -> `None`, mai un'eccezione. Un guasto d'infrastruttura non
deve trasformarsi in rifiuti (vedi `src/graph/tools/page_judge.py`).
"""

from __future__ import annotations

import logging

import fitz

from src.ui.page_render import resolve_pdf   # UNICO percorso di risoluzione dei PDF

logger = logging.getLogger(__name__)

# Tetto del cartiglio passato al giudice. I cartigli veri sono 50-80 char; una
# pagina-tabella ne ha migliaia e va troncata (scelta MISURATA, vedi docstring:
# a 400 si perdeva CV15, a 1500 rientra e nessun caso peggiora).
PAGE_TITLE_MAX_CHARS = 1500

# Righe con meno lettere di questa soglia sono numeri-pallino di un esploso,
# non testo: scartate (scelta dichiarata, vedi docstring).
MIN_LETTERS_PER_LINE = 3

_SEP = " | "

# Il gate interroga le stesse pagine piu' volte (piu' candidati per query, piu'
# query sullo stesso documento): cache in memoria, che memorizza anche i
# FALLIMENTI (una scansione senza livello testo non va riaperta 30 volte).
_CACHE: dict[tuple[str, int], str | None] = {}


def clear_page_title_cache() -> None:
    """Svuota la cache in memoria (test, o re-ingestion di un documento)."""
    _CACHE.clear()


def title_from_text(raw: str) -> str | None:
    """Cartiglio a partire dal testo grezzo di una pagina. Puro e testabile."""
    kept: list[str] = []
    for line in raw.splitlines():
        line = " ".join(line.split())
        if not line:
            continue
        if sum(1 for ch in line if ch.isalpha()) < MIN_LETTERS_PER_LINE:
            continue
        kept.append(line)
    if not kept:
        return None
    return _SEP.join(kept)[:PAGE_TITLE_MAX_CHARS]


def extract_page_title(doc_stem: str, page: int) -> str | None:
    """Cartiglio della pagina `page` (1-based) di `doc_stem`, o `None`.

    `None` significa "non lo so dire", MAI "pagina non pertinente": a valle il
    verificatore non blocca (degrado permissivo).
    """
    key = (doc_stem, page)
    if key in _CACHE:
        return _CACHE[key]

    title: str | None = None
    pdf = resolve_pdf(doc_stem)
    if pdf is None:
        logger.debug("cartiglio: PDF non risolto per %s", doc_stem)
    else:
        try:
            with fitz.open(pdf) as fdoc:
                if 1 <= page <= fdoc.page_count:
                    title = title_from_text(fdoc[page - 1].get_text())
        except Exception as exc:      # PDF corrotto/protetto: nessun cartiglio
            logger.warning("cartiglio: errore su %s p.%s: %s", doc_stem, page, exc)

    _CACHE[key] = title
    return title
