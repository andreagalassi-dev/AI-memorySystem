"""Epica F.1a — Query contextualization: riformulazione PURA del follow-up.

Un follow-up ellittico ("e per quello nuovo?") non e' una query autonoma: il
grafo tri-intent, che e' STATELESS e grounded, non saprebbe cosa cercare. Qui la
cronologia serve a UNA SOLA cosa: riscrivere il follow-up in una query autonoma e
completa, risolvendo pronomi/ellissi con entita' GIA' PRESENTI nel dialogo. Il
grafo a valle gira sulla query RISOLTA e resta INVARIATO.

Principio non negoziabile ("giusto, oppure 'non lo so', mai sbagliato in
silenzio"): la riformulazione e' un LLM e puo' sbagliare. Due guardie
DETERMINISTICHE non-LLM la presidiano PRIMA che la query risolta tocchi il grafo:

  - M1 (guardia VERBATIM): il rewrite NON puo' introdurre identificatori
    (codici/part-number) che non compaiono nel dialogo o nella domanda originale.
    Il confronto e' per INSIEME di identificatori CANONICALIZZATI (`canonical`
    del ramo SQL: upper + solo [0-9A-Z]), non per sottostringa: "GM-41013" e
    "gm41013" sono lo STESSO codice di "GM41013" e passano; "GM4101", "M41013",
    "4101" sono codici DIVERSI e non passano. Un codice inventato = allucinazione
    a monte: si SCARTA il rewrite e si prosegue sulla query originale.
  - M4 (guardia LINGUA): il rewrite deve mantenere la lingua della domanda. Vedi
    nota sotto sul perche' oggi e' presidiata dal solo prompt.

Fail-safe totale: qualunque dubbio (nessun contesto, errore LLM, guardia
scattata) => si ritorna la query ORIGINALE invariata. Mai un rewrite dubbio entra
nel grafo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.graph.context import ConversationContext
from src.graph.llm import LanguageModel
# SOLA LETTURA dalla normalizzazione codici usata a valle dal ramo STRUCTURED
# (lookup su `righe_listino`): la guardia M1 deve confrontare gli identificatori
# CON LA STESSA canonicalizzazione del SQL, altrimenti una grafia che il DB
# considera uguale (GM-41013 == GM41013) verrebbe letta qui come diversa, e una
# diversa (GM4101 vs GM41013) potrebbe passare. NON riscrivere/copiare.
from src.ingestion.codes import canonical
# SOLA LETTURA dal router deterministico (non-LLM): stesso concetto di
# "identificatore" (token con cifre) usato per riconoscere i codici. NON lo
# modifichiamo — riusiamo il suo regex per coerenza tra router e guardia M1.
from src.retrieval.router import TOKEN_RE, _HAS_DIGIT

logger = logging.getLogger(__name__)


SYSTEM_REWRITE = (
    "Sei un RIFORMULATORE di domande, NON un assistente: NON rispondi mai alla "
    "domanda. Ti vengono dati lo storico di una conversazione e la domanda "
    "attuale. Riscrivi la domanda attuale come una domanda AUTONOMA e COMPLETA, "
    "risolvendo pronomi ed ellissi ('quello', 'lo stesso', 'e per il nuovo?') "
    "usando SOLO entita' GIA' PRESENTI nella conversazione, riportate VERBATIM "
    "(stessa grafia esatta). NON coniare modelli, codici o part-number nuovi: se "
    "un identificatore non e' gia' nel testo, NON inventarlo. MANTIENI la lingua "
    "della domanda attuale. Se la domanda e' gia' autonoma, oppure cambia "
    "argomento rispetto alla conversazione, restituiscila INVARIATA. Rispondi "
    "SOLO con la domanda riformulata, senza spiegazioni, senza virgolette, "
    "nient'altro."
)


@dataclass
class ResolvedQuery:
    """Esito della riformulazione.

    `resolved`: query che entra nel grafo (la riformulata SE accettata, altrimenti
    l'originale). `original`: la domanda dell'utente. `changed`: se il grafo vede
    un testo diverso dall'originale. `reason`: motivo auditabile. `influenced`:
    entita'/token del contesto usati (per audit e futuro feedback)."""

    resolved: str
    original: str
    changed: bool
    reason: str
    influenced: tuple[str, ...] = field(default=())


def _identifiers(text: str) -> list[str]:
    """Gli 'identificatori' di un testo: token con cifre, len>=2, senza virgole
    (i prezzi hanno la virgola). Stesso criterio del router (`_codey`)."""
    out = []
    for t in TOKEN_RE.findall(text or ""):
        if len(t) >= 2 and bool(_HAS_DIGIT.search(t)) and "," not in t:
            out.append(t)
    return out


def contextualize_query(query: str, context: ConversationContext,
                        llm: LanguageModel) -> ResolvedQuery:
    """Riformula un follow-up in query autonoma (PURA, non-fatale, guardata).

    Ritorna SEMPRE una `ResolvedQuery`. In ogni caso di dubbio -> query originale.
    """
    # 0. Nessun contesto -> niente da risolvere. NIENTE LLM.
    if context is None or context.is_empty():
        return ResolvedQuery(query, query, changed=False, reason="no-context")

    # 1. Prompt VINCOLATO: solo i content dei turni, prefissati dal role.
    user_lines = [f"{t.role}: {t.content}" for t in context.turns]
    user_lines.append(f"Domanda attuale: {query}")
    user = "\n".join(user_lines)

    # 2. Chiamata LLM — non-fatale: qualunque errore -> originale invariata.
    try:
        resolved = llm.generate(SYSTEM_REWRITE, user)
    except Exception as exc:  # fail-safe: modello giu'/timeout
        logger.warning("contextualize: LLM fallito (%s) -> query invariata", exc)
        return ResolvedQuery(query, query, changed=False, reason="llm-error")
    resolved = (resolved or "").strip()

    # 3. GUARDIA VERBATIM (M1): nessun identificatore nuovo. Deterministica.
    #    Il confronto e' fra INSIEMI DI IDENTIFICATORI CANONICALIZZATI, NON fra
    #    sottostringhe di un blob di testo. La differenza non e' cosmetica: con
    #    la ricerca a sottostringa, un dialogo che contiene "GM41013" lasciava
    #    passare i rewrite con "GM4101" (prefisso), "M41013" (suffisso) o "4101"
    #    (frammento) — codici DIVERSI, che a valle possono esistere davvero a
    #    catalogo e produrre una risposta ALTA ma SBAGLIATA (silent-wrong).
    #    La canonicalizzazione e' quella del ramo SQL (`canonical`: upper +
    #    rimozione di tutto cio' che non e' [0-9A-Z]), quindi:
    #      - "GM-41013" e "gm41013" SONO lo stesso identificatore di "GM41013"
    #        (grafia/case diversi, stesso codice per il lookup a valle):
    #        accettati, non sono invenzioni. Coerenza voluta col DB, non un buco.
    #      - "GM4101", "M41013", "4101", "ABC124" NON sono "GM41013": invenzioni,
    #        il rewrite si scarta e si prosegue sulla query originale.
    #    allowed_text = tutti i content dei turni + la query originale.
    allowed_text = " ".join(t.content for t in context.turns) + " " + query
    allowed_ids = {canonical(t) for t in _identifiers(allowed_text)}
    invented = [tok for tok in _identifiers(resolved)
                if canonical(tok) not in allowed_ids]
    if invented:
        toks = ",".join(invented)
        logger.warning(
            "contextualize: rewrite SCARTATO, identificatore(i) inventato(i) %s "
            "non presenti nel contesto -> query invariata", toks)
        return ResolvedQuery(query, query, changed=False,
                             reason=f"invented-id:{toks}")

    # 4. GUARDIA LINGUA (M4): OMESSA per scelta. Non introduciamo dipendenze
    #    nuove (nessun langdetect nel progetto) e una euristica fatta in casa
    #    sarebbe inaffidabile su query corte IT/EN/DE con codici. La lingua e'
    #    gia' presidiata dal prompt (SYSTEM_REWRITE: "MANTIENI la lingua"). Se in
    #    futuro sara' disponibile un rilevatore affidabile, qui si scarta con
    #    reason="lang-switch".

    # 5. changed? (case/space-insensitive)
    if not resolved or resolved.lower().split() == query.lower().split():
        return ResolvedQuery(query, query, changed=False, reason="no-op")

    # 6. Rewrite accettato.
    influenced = tuple(_identifiers(" ".join(t.content for t in context.turns)))
    return ResolvedQuery(resolved=resolved, original=query, changed=True,
                         reason="resolved", influenced=influenced)
