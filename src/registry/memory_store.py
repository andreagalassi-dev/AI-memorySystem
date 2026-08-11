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

F.2a-2 aggiunge due cose, senza toccare l'RBAC:
  - IDENTITA'/PROVENIENZA dell'hint (`hint_id`, `created_at`, `intent`), per
    l'audit (`hint#<id>`), per la trasparenza verso l'utente e, in F.2b, per la
    revalidazione;
  - un PRE-FILTRO TESTUALE opzionale (parametro `query`), che restringe i
    candidati per RECALL.

F.2a-3 sostituisce quel pre-filtro (ILIKE + stoplist IT/EN/DE curata a mano,
importata da `src.graph.memory_policy`) col FULL-TEXT SEARCH di Postgres, e vi
aggiunge un RANKING. Motivi:
  - la stoplist a mano e' insostenibile — 90 parole da manutenere, gia' bucata
    da un tester — mentre Postgres porta stopword E stemming per lingua,
    mantenuti da altri;
  - la dipendenza `registry -> graph` sparisce.

IMPORTANT: il ranking (e il filtro testuale) sono BEST-EFFORT e **NON sono un
controllo di sicurezza**. La sicurezza e' STRUTTURALE — dopo il pivot la
memoria non tocca mai la query — e, per cio' che viene MOSTRATO all'utente, sta
nel `RevalidationGate` (F.2b), che ri-verifica l'evidenza viva col ruolo di
sessione. Un ordinamento imperfetto produce una card poco pertinente (rumore
visibile), mai una risposta sbagliata ne' un leak.

Il modulo e' SOLO LETTURA sul DB: esegue esclusivamente SELECT.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import psycopg

# Le tre lingue del corpus (IT/EN/DE). Sono NOMI DI CONFIGURAZIONE Postgres,
# costanti scritte da noi: non arrivano mai dall'utente, quindi possono stare
# in una f-string. I VALORI (la domanda) viaggiano sempre come parametri.
_FTS_CONFIGS = ("italian", "english", "german")

# SQL parametrizzato (mai f-string sui VALORI): il pre-filtro RBAC descritto nel
# docstring. Diviso in testa/coda perche' fra i due si inserisce il filtro
# TESTUALE, e la coda cambia a seconda che ci sia o no una domanda.
_RECALL_HEAD = """
    SELECT id, query, risposta, citazioni, conferme_count, livello_memoria,
           created_at, contesto->>'intent' AS intent
    FROM public.feedback AS f
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
"""

# Senza domanda: nessun filtro testuale, nessun rank. `id DESC` chiude sempre
# l'ordinamento (vedi `_ORDER_TIEBREAK`).
_ORDER_TIEBREAK = "conferme_count DESC NULLS LAST, created_at DESC, id DESC"
_RECALL_TAIL_NO_QUERY = f"""
    ORDER BY {_ORDER_TIEBREAK}
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
    # F.2a-2 — identita' e provenienza dell'hint. `hint_id` e' la PK della riga
    # di `feedback`: serve all'audit (`hint#<id>`, tracciabile dall'utente) e,
    # in F.2b, alla revalidazione contro l'evidenza viva. `created_at` datera'
    # il binding nel messaggio di trasparenza. `intent` e' `contesto->>'intent'`
    # e su tutte le righe STORICHE e' NULL (il campo non veniva scritto).
    hint_id: int | None = None
    created_at: datetime | None = None
    intent: str | None = None


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


def _lexeme_overlap_clause() -> str:
    """Filtro testuale FTS: i lessemi in comune in ALMENO UNA delle tre lingue.

    Semantica OR: basta UN termine condiviso perche' la riga sia candidata
    (filtro di RECALL, non di precisione — a decidere l'ordine e' il rank).

    Perche' l'intersezione di ARRAY di lessemi (`&&`) e non `to_tsquery`:
    `&&` non fa PARSING di una tsquery, quindi non puo' sollevare su testo
    utente qualsiasi. Verificato sul DB reale: `to_tsquery('italian', ...)`
    solleva SyntaxError su `''' \\ !!! ::: &&& |||`, il confronto con `&&` no.
    """
    ors = "\n         OR ".join(
        f"array(select lexeme from unnest(to_tsvector('{cfg}', f.query)))"
        f" && array(select lexeme from unnest(to_tsvector('{cfg}', %(q)s)))"
        for cfg in _FTS_CONFIGS
    )
    return f"      AND (\n            {ors}\n      )\n"


def _rank_expr() -> str:
    """Rank = il migliore dei tre `ts_rank` (IT/EN/DE).

    `plainto_tsquery` (non `to_tsquery`) tratta l'input come testo semplice e
    non interpreta operatori: come `&&`, non solleva su input avversari.
    """
    ranks = ",\n              ".join(
        f"ts_rank(to_tsvector('{cfg}', f.query), plainto_tsquery('{cfg}', %(q)s))"
        for cfg in _FTS_CONFIGS
    )
    return f"GREATEST(\n              {ranks}\n           )"


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

        Il pre-filtro RBAC e' nel WHERE (vedi docstring del modulo) e NON e'
        toccato: il filtro testuale e' un AND in PIU', quindi puo' solo
        restringere l'insieme visibile, mai allargarlo.

        `query` (F.2a-3): se valorizzata,
          - FILTRA le righe che condividono almeno un lessema con la domanda in
            almeno una delle tre lingue del corpus (IT/EN/DE);
          - ORDINA per `rank` FTS decrescente, poi
            `conferme_count DESC NULLS LAST, created_at DESC, id DESC`.
        Se la domanda non produce alcun lessema (soli spazi, sole stopword) il
        filtro non trova nulla e il risultato e' vuoto: fail-closed, coerente
        con "nel dubbio non si propone".
        Con `query=None` non si filtra e non si ranka.

        `id DESC` chiude SEMPRE l'ordinamento: senza, a parita' di
        `created_at` (e `now()` e' costante dentro una transazione, quindi la
        parita' e' comune) l'ordine sarebbe arbitrario e la card mostrata
        cambierebbe fra due run identici.

        IMPORTANT: ranking e filtro sono BEST-EFFORT, **non** un controllo di
        sicurezza (vedi docstring del modulo).
        """
        params: dict[str, object] = {"role": role, "utente": utente,
                                     "limit": limit}
        if query is None:
            sql = _RECALL_HEAD + _RECALL_TAIL_NO_QUERY
        else:
            params["q"] = query
            sql = (
                _RECALL_HEAD
                + _lexeme_overlap_clause()
                + f"    ORDER BY {_rank_expr()} DESC NULLS LAST,\n"
                + f"             {_ORDER_TIEBREAK}\n"
                + "    LIMIT %(limit)s\n"
            )
        rows = conn.execute(sql, params).fetchall()

        hints: list[MemoryHint] = []
        for row in rows:
            try:
                (hint_id, q, risposta, citazioni, conferme_count, livello,
                 created_at, intent) = row
                hints.append(
                    MemoryHint(
                        query=q,
                        evidence_ids=_parse_evidence_ids(citazioni),
                        conferme_count=int(conferme_count or 0),
                        livello=livello,
                        risposta=risposta,
                        hint_id=int(hint_id),
                        created_at=created_at,
                        intent=intent,
                    )
                )
            except Exception:
                # una riga anomala non deve mai far crashare l'intero recall.
                continue
        return hints
