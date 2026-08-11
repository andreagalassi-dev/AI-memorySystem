"""Epica F.1a — Contesto conversazionale TIPIZZATO (la giuntura del futuro).

Il grafo tri-intent (`answer`) e' STATELESS: risponde a una query autonoma e
grounded. Il contesto conversazionale NON entra nel grafo: serve SOLO a
riformulare un follow-up ellittico in una query autonoma (query
contextualization, vedi `contextualize.py`). Il grafo a valle resta INVARIATO.

Questo modulo definisce i TIPI del contesto. Oggi il contesto e' costruito dal
solo dialogo short-term (gli ultimi turni). Domani questa stessa classe sara' il
punto d'innesto — SENZA cambio di firma — per la memoria long-term e il profilo
utente (Epica F successiva): i campi/placeholder futuri sono documentati ma NON
implementati ora.

PRECEDENZA dei conflitti di contesto (regola deterministica per il futuro, quando
piu' fonti potranno contribuire un'entita' o un vincolo):

    evidenza live > turno corrente esplicito > storia short-term
                  > memoria long-term > profilo utente

Cioe': cio' che l'evidenza recuperata dimostra batte quanto l'utente dice adesso;
quello che l'utente dice adesso batte la storia; la storia batte la memoria di
lungo periodo; e il profilo utente e' l'ultima risorsa. Oggi esiste solo la
storia short-term, ma la regola e' fissata perche' le fonti future si innestino
in modo prevedibile e auditabile.
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
    `role`: il ruolo RBAC della sessione (per filtrare, domani, la memoria).

    PLACEHOLDER tipizzati FUTURI (Epica F, NON implementati ora): la memoria
    long-term (fatti persistenti filtrati per RBAC) e il profilo utente
    (preferenze/default) si aggiungeranno come CAMPI di questa stessa classe. La
    classe e' il punto d'innesto: chi consuma il contesto (`contextualize_query`,
    `answer`) non cambiera' firma quando arriveranno.
    """

    turns: tuple[Turn, ...] = ()
    role: Role | None = None

    def is_empty(self) -> bool:
        return not self.turns


class ContextAssembler:
    """Assembla un `ConversationContext` dai turni grezzi della sessione.

    Oggi costruisce il contesto dal SOLO dialogo short-term (gli ultimi `window`
    turni). Domani, con la memoria long-term e il profilo (Epica F), aggiungera'
    fonti filtrate per RBAC dietro questa STESSA firma: `build(...)` non cambiera'.
    Il punto in cui piu' fonti si comporranno e' regolato dalla precedenza
    documentata nel modulo.
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
