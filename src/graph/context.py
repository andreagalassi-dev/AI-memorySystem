"""Epica F.1a — Contesto conversazionale TIPIZZATO (la giuntura del futuro).

Il grafo tri-intent (`answer`) e' STATELESS: risponde a una query autonoma e
grounded. Il contesto conversazionale NON entra nel grafo: serve SOLO a
riformulare un follow-up ellittico in una query autonoma (query
contextualization, vedi `contextualize.py`). Il grafo a valle resta INVARIATO.

Questo modulo definisce i TIPI del contesto. Il contesto e' costruito dal SOLO
dialogo short-term (gli ultimi turni): e' l'unica fonte che alimenta la
riformulazione della query.

NOTA DI PROGETTO — la memoria long-term NON e' un campo di questa classe, e non
lo diventera'. Un campo qui significherebbe una fonte che raggiunge
`contextualize.py`, cioe' la memoria che rientra sul percorso della query: e'
stato provato sperimentalmente che basta una pertinenza euristica perche' il
codice di un pezzo DIVERSO finisca nella query riscritta e produca una risposta
ALTA e sbagliata (vedi la nota estesa in `contextualize.py`). La memoria vive
come SUGGERIMENTO mostrato all'utente accanto alla risposta, fuori di qui.
Resta futuro il profilo utente (preferenze/default).

PRECEDENZA dei conflitti di contesto (regola deterministica per il futuro, quando
piu' fonti potranno contribuire un'entita' o un vincolo):

    evidenza live > turno corrente esplicito > storia short-term
                  > memoria long-term > profilo utente

Cioe': cio' che l'evidenza recuperata dimostra batte quanto l'utente dice adesso;
quello che l'utente dice adesso batte la storia; la storia batte la memoria di
lungo periodo; e il profilo utente e' l'ultima risorsa. Oggi esiste solo la
storia short-term, ma la regola e' fissata perche' le fonti future si innestino
in modo prevedibile e auditabile. Nota che la memoria long-term sta SOTTO la
storia short-term proprio perche' e' la piu' debole delle fonti: un binding
vecchio non sa nulla di cio' che l'utente sta chiedendo adesso.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.common.roles import Role


@dataclass(frozen=True)
class Turn:
    """Un turno del dialogo.

    - `role`: "user" | "assistant".
    - `content`: il testo del turno.
    - `evidence_ids`: citazioni (doc, page) dell'evidenza di QUEL turno. Serve
      all'anti-laundering (Epica F): un follow-up non puo' "ripulire" un'entita'
      che nessuna evidenza reale ha mai supportato. Oggi tracciato, domani usato.
    """

    role: str
    content: str
    evidence_ids: tuple[tuple[str, int], ...] = ()


@dataclass(frozen=True)
class ConversationContext:
    """Il contesto conversazionale, immutabile.

    `turns`: gli ultimi turni del dialogo short-term (finestra scorrevole).
    `role`: il ruolo RBAC della sessione (per filtrare, domani, la memoria
      quando verra' MOSTRATA all'utente — mai per riformulare la query).

    PLACEHOLDER tipizzato ancora FUTURO: il profilo utente (preferenze/default),
    ultimo anello della precedenza documentata nel modulo. La memoria long-term
    NON e' un placeholder di questa classe: vedi la nota di progetto in testa.

    `is_empty()` guarda SOLO i turni, e questa e' una SCELTA, non una svista:
    senza turni non esiste un follow-up ellittico da risolvere, e la
    riformulazione e' l'unico consumatore del contesto. Un contesto senza turni
    e' quindi vuoto qualunque altra cosa si sappia della conversazione, e
    `pipeline.answer` salta del tutto la contestualizzazione.
    """

    turns: tuple[Turn, ...] = ()
    role: Role | None = None

    def is_empty(self) -> bool:
        return not self.turns


class ContextAssembler:
    """Assembla un `ConversationContext` dai turni grezzi della sessione.

    Costruisce il contesto dal SOLO dialogo short-term (gli ultimi `window`
    turni). Se un giorno arrivera' il profilo utente, si innestera' dietro
    questa STESSA firma: `build(...)` non cambiera'. Il punto in cui piu' fonti
    si comporranno e' regolato dalla precedenza documentata nel modulo. La
    memoria long-term non passa di qui: non alimenta la riformulazione.
    """

    def build(self, raw_turns: list[dict], role: Role | None = None,
              window: int = 6) -> ConversationContext:
        """Costruisce il contesto dagli ultimi `window` turni grezzi.

        `raw_turns`: lista di dict con chiavi "role", "content" e opzionalmente
        "evidence_ids". I turni oltre la finestra sono scartati (short-term).
        """
        recent = raw_turns[-window:] if window and window > 0 else list(raw_turns)
        turns = tuple(
            Turn(
                role=str(t.get("role", "")),
                content=str(t.get("content", "")),
                evidence_ids=tuple(
                    (str(d), int(p)) for d, p in (t.get("evidence_ids") or ())
                ),
            )
            for t in recent
        )
        return ConversationContext(turns=turns, role=role)
