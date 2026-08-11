"""F.2a-1 — Recupero (read-only) dei binding confermati: MemoryStore.

Parte del principio "grounded memory: la memoria PROPONE, l'evidenza viva
DISPONE". Questo modulo costruisce SOLO il RECUPERO dei binding che gli utenti
hanno CONFERMATO, per alimentare (nei punti F.2 successivi) la
contestualizzazione come HINT — mai come risposta.

Questo store applica il PRE-FILTRO RBAC (filtro "(a)"): un WHERE parametrizzato
che restituisce solo i binding potenzialmente visibili a questo utente/ruolo.
NON e' l'RBAC autoritativo: l'RBAC sull'evidenza viva viene RI-applicato al
momento del serve (F.2b). Questo store NON serve MAI dati veri — fornisce solo
suggerimenti (hint) su cosa e' stato confermato in passato.

Regole di visibilita' (fail-closed sulle memorie CONDIVISE):
  - `verdetto = 'conferma'` (solo binding confermati);
  - `stato_moderazione <> 'rejected'`: una conferma rifiutata da un moderatore
    non deve MAI ricomparire, nemmeno al suo autore;
  - le proprie conferme (livello personale) sono visibili all'autore purche'
    non rifiutate (pending/approved vanno bene);
  - una memoria CONDIVISA (categoria/aziendale) richiede APPROVAZIONE esplicita
    (`stato_moderazione = 'approved'`): il default 'pending' NON condivide.

Il modulo e' SOLO LETTURA sul DB: esegue esclusivamente SELECT.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg

# SQL parametrizzato (mai f-string): il pre-filtro RBAC descritto nel docstring.
_RECALL_SQL = """
    SELECT query, risposta, citazioni, conferme_count, livello_memoria
    FROM public.feedback
    WHERE verdetto = 'conferma'
      AND stato_moderazione <> 'rejected'
      AND (
            utente = %(utente)s
         OR (livello_memoria = 'categoria'
             AND ruolo = %(role)s
             AND stato_moderazione = 'approved')
         OR (livello_memoria = 'aziendale'
             AND stato_moderazione = 'approved')
      )
    ORDER BY conferme_count DESC NULLS LAST, created_at DESC
    LIMIT %(limit)s
"""


@dataclass(frozen=True)
class MemoryHint:
    """Un binding confermato, come SUGGERIMENTO (mai come risposta).

    `evidence_ids` e' ricavato dal jsonb `citazioni` (lista di [doc, page]).
    """

    query: str
    evidence_ids: tuple[tuple[str, int], ...]
    conferme_count: int
    livello: str | None
    risposta: str | None = None


def _parse_evidence_ids(citazioni: object) -> tuple[tuple[str, int], ...]:
    """Ricava le coppie (doc, page) da `citazioni` jsonb.

    Robusto a null/malformati: qualunque forma non riconosciuta produce tupla
    vuota (mai eccezioni verso il chiamante). Accetta sia liste posizionali
    [doc, page] sia dict {"doc": ..., "page": ...}.
    """
    out: list[tuple[str, int]] = []
    if not isinstance(citazioni, (list, tuple)):
        return ()
    for item in citazioni:
        try:
            if isinstance(item, dict):
                doc = item["doc"]
                page = item["page"]
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                doc, page = item[0], item[1]
            else:
                continue
            out.append((str(doc), int(page)))
        except (KeyError, ValueError, TypeError):
            # singola citazione malformata: la salto, non rompo l'hint.
            continue
    return tuple(out)


class MemoryStore:
    """Recupero read-only, RBAC-pre-filtrato, dei binding confermati."""

    def recall(
        self,
        conn: psycopg.Connection,
        *,
        role: str,
        utente: str,
        query: str | None = None,
        limit: int = 5,
    ) -> list[MemoryHint]:
        """Restituisce i binding confermati visibili a questo utente/ruolo.

        Il pre-filtro RBAC e' nel WHERE (vedi docstring del modulo). Ordina per
        `conferme_count DESC NULLS LAST, created_at DESC` e taglia a `limit`.

        `query` e' accettato ma per ora IGNORATO.
        TODO F.2b: ranking semantico sul testo della query.
        """
        rows = conn.execute(
            _RECALL_SQL,
            {"role": role, "utente": utente, "limit": limit},
        ).fetchall()

        hints: list[MemoryHint] = []
        for row in rows:
            try:
                q, risposta, citazioni, conferme_count, livello = row
                hints.append(
                    MemoryHint(
                        query=q,
                        evidence_ids=_parse_evidence_ids(citazioni),
                        conferme_count=int(conferme_count or 0),
                        livello=livello,
                        risposta=risposta,
                    )
                )
            except Exception:
                # una riga anomala non deve mai far crashare l'intero recall.
                continue
        return hints
