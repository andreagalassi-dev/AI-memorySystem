"""Epica F.2a-2 — POLITICA di autorizzazione della memoria long-term.

Principio: **la memoria PROPONE, l'evidenza viva DISPONE**. La memoria alimenta
SOLO la riformulazione della query (`contextualize.py`), MAI il contenuto della
risposta: la risposta resta grounded sull'evidenza recuperata dal grafo, che
qui non viene toccato.

Il rischio da cui questo modulo difende (ed e' il motivo per cui esiste): un
hint di memoria inietta un codice nella query riformulata, il ramo STRUCTURED
trova un record REALE ma relativo a un pezzo SBAGLIATO, e risponde ALTA. Il
fail-closed a valle NON lo intercetta, perche' il dato esiste davvero. L'unico
punto in cui quell'errore si puo' fermare e' PRIMA, qui.

Il modulo e' PURO: nessun DB, nessun LLM, nessuna rete, nessuno stato. E'
interamente decidibile a tavolino, quindi interamente testabile a tavolino.

Le quattro regole, applicate IN QUEST'ORDINE:

  H2.a  esclusione per conflitto  -> se il dialogo contiene GIA' un id, la
        memoria tace del tutto;
  H2.d  pertinenza con soglia     -> almeno 2 termini significativi in comune
        con la SOLA domanda corrente;
  H2.c  origine ristretta         -> l'id si prende solo da `hint.query`, e un
        hint autorizza AL MASSIMO un id;
  H2.b  top-1 stretto             -> pari-merito con id diversi = "non lo so".

In caso di dubbio: nessuna autorizzazione. La funzione non solleva MAI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Sequence

from src.graph.identifiers import identifiers
# SOLA LETTURA dalla normalizzazione codici del ramo SQL: l'identificatore che
# autorizziamo deve essere confrontabile con quelli della guardia M1 e con il
# lookup a valle. Stessa `canonical` = stessa nozione di "e' lo stesso codice".
from src.ingestion.codes import canonical
# SOLA LETTURA dal router deterministico: stessa tokenizzazione usata per
# riconoscere gli identificatori, cosi' "termine" e "identificatore" partono
# dalla stessa segmentazione del testo.
from src.retrieval.router import TOKEN_RE

if TYPE_CHECKING:  # solo per i tipi: nessuna dipendenza runtime da psycopg
    from src.graph.context import Turn
    from src.registry.memory_store import MemoryHint


# Soglia di pertinenza (H2.d). Due termini significativi in comune sono il
# minimo per distinguere "la stessa domanda di prima" da "una domanda che per
# caso condivide una parola". Con 1 basterebbe "pompa" per legare due pompe
# diverse; con 3 il caso d'uso legittimo (domande corte) non passerebbe mai.
MIN_OVERLAP = 2

# Stoplist CHIUSA IT/EN/DE. Sono le parole che compaiono in QUALUNQUE domanda
# di questo dominio (prezzo/codice/quanto costa/...): lasciarle contare
# gonfierebbe l'overlap fra domande che non hanno NULLA a che vedere fra loro,
# ed e' esattamente cosi' che si autorizza il codice del pezzo sbagliato.
# Oltre a quelle richieste dalla specifica ho aggiunto:
#  - "quali/qual", "come", "dove", "sono", "essere", "avere": interrogativi e
#    verbi di servizio, stesso identico difetto;
#  - "questa/quella/quelle/quelli/questi": deittici, non identificano nulla;
#  - "listino", "catalogo", "tabella", "pagina", "documento", "manuale":
#    metalinguaggio sul CORPUS, presente ovunque e mai discriminante;
#  - "euro", "much", "many", "does", "there", "please", "kannst", "haben",
#    "koennen", "bitte", "diese", "dieser", "dieses", "ersatz", "preise":
#    equivalenti EN/DE dei precedenti.
# NB: la stoplist toglie RUMORE, non e' un filtro di sicurezza; la sicurezza
# sta in H2.a/H2.b/H2.c.
STOPWORDS: frozenset[str] = frozenset({
    # --- italiano
    "prezzo", "prezzi", "costo", "costa", "costano", "quanto", "quanta",
    "quale", "quali", "codice", "codici", "articolo", "articoli", "pezzo",
    "pezzi", "ricambio", "ricambi", "questo", "questa", "questi", "queste",
    "quello", "quella", "quelli", "quelle", "della", "delle", "degli",
    "dello", "come", "dove", "sono", "essere", "avere", "euro", "listino",
    "catalogo", "tabella", "pagina", "documento", "manuale",
    # --- inglese
    "price", "prices", "cost", "costs", "code", "codes", "part", "parts",
    "number", "what", "which", "item", "items", "spare", "much", "many",
    "does", "there", "please", "page", "table", "catalog", "catalogue",
    "manual", "document",
    # --- tedesco
    "preis", "preise", "kosten", "artikel", "welche", "welcher", "welches",
    "wieviel", "stueck", "teil", "teile", "ersatzteil", "ersatz", "diese",
    "dieser", "dieses", "kannst", "haben", "koennen", "bitte", "seite",
    "tabelle", "katalog", "handbuch",
})


@dataclass(frozen=True)
class MemoryAuthorization:
    """Esito della politica: al massimo UN identificatore, sempre motivato.

    `identifier` e' CANONICALIZZATO (`canonical`): None = la memoria tace.
    `reason` e' auditabile e finisce nella riga di audit di `answer`.
    """

    identifier: str | None = None
    hint_id: int | None = None
    hint: "MemoryHint | None" = None
    reason: str = "mem-none"


def significant_terms(text: str) -> set[str]:
    """I termini di `text` che possono DISCRIMINARE fra due domande.

    Lowercase, lunghezza >= 4, non in `STOPWORDS`, non identificatori (i codici
    li tratta H2.c, non la pertinenza: contarli come termini renderebbe
    circolare il punteggio).
    """
    out: set[str] = set()
    for tok in TOKEN_RE.findall(text or ""):
        if identifiers(tok):          # e' un codice: non e' un termine
            continue
        low = tok.lower()
        if len(low) < 4 or low in STOPWORDS:
            continue
        out.add(low)
    return out


def _hint_identifier(hint: "MemoryHint") -> str | None:
    """H2.c — l'UNICO identificatore canonicalizzato di `hint.query`, o None.

    Si guarda SOLO `hint.query`, MAI `hint.risposta`: la risposta e' prosa
    generata da un LLM che l'utente ha confermato NEL COMPLESSO (spesso con un
    click di pollice-su), e contiene varianti, codici di kit e figli di assieme
    che non sono MAI stati oggetto di quella conferma. Trattarli come binding
    significherebbe promuovere a fatto cio' che nessuno ha mai confermato.

    Zero id -> l'hint non autorizza nulla (non c'e' binding).
    Piu' di un id distinto -> nemmeno: quale dei due sarebbe "il" binding? In
    dubbio si tace.
    """
    ids = {canonical(t) for t in identifiers(hint.query or "")}
    ids.discard("")
    if len(ids) != 1:
        return None
    return next(iter(ids))


def memory_authorized_ids(
    question: str,
    turns: "Sequence[Turn] | None",
    hints: "Iterable[MemoryHint] | None",
) -> MemoryAuthorization:
    """Decide se la memoria puo' proporre UN identificatore alla riformulazione.

    Non solleva mai: qualunque anomalia -> nessuna autorizzazione.
    """
    try:
        hint_list = list(hints or ())
        turn_list = list(turns or ())
        q = question or ""

        # Input insufficienti: non c'e' nulla da autorizzare.
        if not hint_list or not q.strip():
            return MemoryAuthorization(reason="mem-none")

        # --- H2.a — ESCLUSIONE PER CONFLITTO (la regola principale) ---------
        # Se l'utente o la storia hanno GIA' nominato un codice, il referente
        # e' determinato: la memoria non ha nulla di legittimo da aggiungere e
        # tutto da corrompere. Questa regola azzera PER COSTRUZIONE la classe
        # di errore piu' pericolosa — la confusione fra prodotti FRATELLI, dove
        # la somiglianza dei testi e' alta proprio perche' i pezzi sono
        # confondibili (stessa serie, stesso nome, codice adiacente).
        # E non costa la feature: il caso d'uso legittimo ("quanto costa?", "e
        # per quello nuovo?") e' per definizione quello in cui NESSUN codice e'
        # presente nel dialogo.
        dialog = " ".join(str(t.content or "") for t in turn_list) + " " + q
        if identifiers(dialog):
            return MemoryAuthorization(reason="mem-skip:dialog-has-id")

        # --- H2.d — PERTINENZA con soglia e stoplist ------------------------
        # L'overlap si misura sulla SOLA domanda corrente, non su tutta la
        # storia: la storia si allunga a ogni turno e gonfierebbe il punteggio
        # di qualunque hint, fino a far "matchare" tutto con tutto.
        q_terms = significant_terms(q)
        candidates: list[tuple[int, str, "MemoryHint"]] = []
        for hint in hint_list:
            score = len(q_terms & significant_terms(getattr(hint, "query", "")))
            if score < MIN_OVERLAP:
                continue
            ident = _hint_identifier(hint)   # H2.c
            if ident is None:
                continue
            candidates.append((score, ident, hint))

        if not candidates:
            return MemoryAuthorization(reason="mem-none:no-relevant-hint")

        # --- H2.b — TOP-1 STRETTO, abort sull'ambiguita' --------------------
        # `sorted` e' stabile: a parita' di score resta l'ordine dello store
        # (conferme_count DESC, created_at DESC).
        candidates.sort(key=lambda c: c[0], reverse=True)
        top_score = candidates[0][0]
        tied = [c for c in candidates if c[0] == top_score]
        if len({c[1] for c in tied}) > 1:
            # Due memorie che propongono due codici sono l'equivalente di "non
            # lo so": si preferisce il silenzio a una scelta a caso fra due
            # pezzi diversi. Se i pari-merito portano lo STESSO id, invece, non
            # c'e' ambiguita' e si autorizza.
            return MemoryAuthorization(reason="mem-abort:ambiguous")

        _, ident, hint = tied[0]
        hint_id = getattr(hint, "hint_id", None)
        return MemoryAuthorization(
            identifier=ident,
            hint_id=hint_id,
            hint=hint,
            reason=f"mem-authorized-id:{ident}@hint#{hint_id}",
        )
    except Exception:  # nessuna anomalia della memoria puo' rompere una risposta
        return MemoryAuthorization(reason="mem-none:error")
