"""F.2b — `RevalidationGate`: "la memoria PROPONE, l'evidenza viva DISPONE".

Nessun hint puo' essere MOSTRATO all'utente senza che l'evidenza che cita sia
stata ri-verificata VIVA, adesso, per l'utente CORRENTE.

Perche' esiste (e perche' e' bloccante dopo il pivot di `d1b2414`)
------------------------------------------------------------------
La memoria non tocca piu' la query: diventera' una CARD di suggerimento
mostrata accanto alla risposta. Ma il testo del binding FINISCE SULLO SCHERMO,
e il pre-filtro RBAC di `memory_store` filtra la RIGA DI FEEDBACK, non
l'EVIDENZA: un binding `aziendale`+`approved` e' visibile a chiunque, anche se
cita un documento riservato ai tecnici. Senza questo gate, quel contenuto
raggiungerebbe un commerciale. Questo modulo e' l'unica cosa che lo impedisce.

Le regole (tutte FAIL-CLOSED: nel dubbio si SCARTA)
---------------------------------------------------
1. `role` deve essere un `Role`, altrimenti `TypeError` — parita' con
   `visual_search` (`src/retrieval/pixelrag/search.py`). Mai un default: un
   default aprirebbe l'accesso.
2. `evidence_ids` vuoto -> `no-evidence`. Un binding senza fonte non e'
   verificabile, e "mai un'affermazione senza fonte" e' il principio del
   progetto.
3. Registro: UNA sola query su `document_versions` per TUTTI gli stem.
   stem assente -> `doc-unknown`; `version_status <> 'current'` ->
   `doc-not-current`; `ingested_at > hint.created_at` -> `stale-binding`;
   `created_at` mancante o non confrontabile -> `unknown-age`.

   *Perche' `ingested_at` e non un hash del contenuto*: nel progetto un hash
   del contenuto del chunk NON esiste. `chunk_id` e' POSIZIONALE e
   `point_id = uuid5(chunk_id)`, quindi un re-ingest sovrascrive il contenuto
   sotto un id STABILE: "il chunk esiste ancora" non prova NIENTE. La
   freschezza della conferma rispetto all'ultima ingestione e' il sostituto
   onesto disponibile oggi.
4. Visibilita' VIVA su Qdrant col ruolo di SESSIONE (mai `feedback.ruolo`:
   sarebbe ereditarieta' di privilegio — e infatti `MemoryHint` non trasporta
   nemmeno quel campo). Il filtro RBAC e' RIUSATO da
   `src.retrieval.search.access_filter`, mai riscritto: se un giorno cambia la
   semantica dei ruoli deve cambiare in UN posto solo, e il gate non deve
   poter essere piu' permissivo del retrieval. Stem non visibile ->
   `rbac-or-missing`.
5. TUTTI gli stem di un hint devono passare: uno solo che fallisce scarta
   l'INTERO hint (AND, mai validazione parziale).
6. Budget complessivo (`budget_s`, default 2.0s). Superato il budget, o su
   QUALUNQUE eccezione (Qdrant giu', DB giu'): `kept = []`, tutti gli hint in
   `dropped` con `gate-unavailable`, un warning nel log, nessuna eccezione
   propagata. Il gate non deve MAI rompere una risposta. Unica eccezione che
   esce: il `TypeError` sul ruolo, che e' un errore del CHIAMANTE.

Cosa questo gate NON fa
-----------------------
NON ri-esegue il lookup SQL su `righe_listino` (o su qualunque dato di
listino). La memoria non serve VALORI: li ricalcola comunque il grafo, dal
dato vivo. Ri-eseguirlo qui sarebbe teatro pagato con un round-trip. E' una
domanda che tornera': la risposta e' scritta qui.

Il gate NON conosce `ConversationContext`, `ContextAssembler`, ne' la
pipeline: e' richiamabile identico da un secondo punto in F.3.

Chiave Qdrant: si filtra su `doc`, MAI su `doc_stem`. Misurato sulla
collezione reale (`aike_chunks`, 199447 punti): `doc_stem` e' ASSENTE dal
payload di molti punti — `doc_stem='flow_exploded_2022'` restituisce 0 punti,
`doc='flow_exploded_2022'` ne restituisce 21. Un gate su `doc_stem`
scarterebbe tutto. C'e' un test di regressione sul Qdrant reale
(`test_INTEGRAZIONE_facet_su_doc_stem_darebbe_zero...`).

Il modulo e' SOLO LETTURA: SELECT su Postgres, facet su Qdrant.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

import psycopg
from qdrant_client import models

from src.common.roles import Role
from src.ingestion.embedding import COLLECTION
from src.registry.memory_store import MemoryHint, MemoryStore
from src.retrieval.search import access_filter

log = logging.getLogger(__name__)

CURRENT = "current"

# Motivi di scarto. Stringhe stabili: finiranno nell'audit e (in F.2c/F.2d) in
# una diagnostica per il moderatore.
REASON_NO_EVIDENCE = "no-evidence"
REASON_UNKNOWN_AGE = "unknown-age"
REASON_DOC_UNKNOWN = "doc-unknown"
REASON_DOC_NOT_CURRENT = "doc-not-current"
REASON_STALE = "stale-binding"
REASON_RBAC = "rbac-or-missing"
REASON_UNAVAILABLE = "gate-unavailable"


@dataclass(frozen=True)
class DroppedHint:
    """Un hint scartato e il PERCHE'. Il motivo non e' cosmetico: senza, un
    gate che scarta tutto e un gate che funziona sono indistinguibili."""

    hint: MemoryHint
    reason: str


class _BudgetExceeded(TimeoutError):
    """Interno: fa cadere il gate nel ramo fail-safe come qualsiasi guasto."""


class RevalidationGate:
    """Ri-verifica gli hint contro l'evidenza VIVA, per il ruolo di sessione."""

    def __init__(self, *, collection: str = COLLECTION,
                 version_status: str = CURRENT) -> None:
        self.collection = collection
        self.version_status = version_status

    # ------------------------------------------------------------ pubblico --

    def validate(
        self,
        hints: Iterable[MemoryHint],
        *,
        conn: psycopg.Connection,
        qc,
        role: Role,
        budget_s: float = 2.0,
    ) -> tuple[list[MemoryHint], list[DroppedHint]]:
        """Ritorna `(sopravvissuti, scartati)`. Vedi le regole nel docstring."""
        if not isinstance(role, Role):  # fail-closed: mai un default
            raise TypeError(f"role deve essere Role, non {type(role).__name__}")

        hints = list(hints)
        if not hints:
            return [], []

        deadline = time.monotonic() + budget_s

        # Regole 2 e 3-bis: si decidono senza toccare la rete.
        pending: list[MemoryHint] = []
        dropped: list[DroppedHint] = []
        for h in hints:
            if not h.evidence_ids:
                dropped.append(DroppedHint(h, REASON_NO_EVIDENCE))
            elif h.created_at is None:
                dropped.append(DroppedHint(h, REASON_UNKNOWN_AGE))
            else:
                pending.append(h)
        if not pending:
            return [], dropped

        stems = sorted({doc for h in pending for doc, _page in h.evidence_ids})

        try:
            self._check_budget(deadline)
            versions = self._load_versions(conn, stems)
            self._check_budget(deadline)
            visible = self._visible_stems(qc, stems, role)
            self._check_budget(deadline)
        except Exception as exc:
            # Fail-safe TOTALE: se il gate non sa decidere, non passa NULLA —
            # nemmeno gli hint gia' valutati prima del guasto.
            log.warning(
                "RevalidationGate non disponibile (%s: %s): scartati tutti i "
                "%d hint proposti", type(exc).__name__, exc, len(hints))
            return [], [DroppedHint(h, REASON_UNAVAILABLE) for h in hints]

        kept: list[MemoryHint] = []
        for h in pending:
            reason = self._reject_reason(h, versions, visible)
            if reason is None:
                kept.append(h)
            else:
                dropped.append(DroppedHint(h, reason))
        return kept, dropped

    # ------------------------------------------------------------- interno --

    @staticmethod
    def _check_budget(deadline: float) -> None:
        if time.monotonic() > deadline:
            raise _BudgetExceeded("budget di revalidazione esaurito")

    @staticmethod
    def _load_versions(
        conn: psycopg.Connection, stems: Sequence[str]
    ) -> dict[str, tuple[str, datetime | None]]:
        """UNA sola query per TUTTI gli stem di TUTTI gli hint."""
        rows = conn.execute(
            "SELECT doc_stem, version_status, ingested_at "
            "FROM document_versions WHERE doc_stem = ANY(%(stems)s)",
            {"stems": list(stems)},
        ).fetchall()
        return {r[0]: (r[1], r[2]) for r in rows}

    def _visible_stems(self, qc, stems: Sequence[str], role: Role) -> set[str]:
        """Stem che hanno ALMENO un punto visibile al ruolo di SESSIONE.

        Strada scelta: **facet** sulla chiave `doc` (verificata contro il
        Qdrant reale, qdrant-client 1.18.0), non uno scroll unico con
        `should=[doc=...]`: con lo scroll, un solo documento capiente puo'
        consumare tutto il `limit` e far sembrare ASSENTI gli altri stem, che
        verrebbero scartati per errore. Il facet risponde invece per VALORE.

        `limit=len(stems)` non puo' troncare: il `MatchAny` restringe gia' i
        valori possibili esattamente a quegli stem. `exact=True` perche' un
        conteggio approssimato potrebbe omettere un valore raro e scartare un
        hint valido (misurato sul reale: 12ms contro 3ms su 199447 punti).

        Il filtro RBAC NON e' riscritto: si parte da `access_filter` e vi si
        AGGIUNGE la condizione sul documento. `model_copy` preserva ogni altro
        campo del filtro, cosi' un futuro irrigidimento di `access_filter`
        arriva qui da solo invece di essere silenziosamente perso.
        """
        base = access_filter(self.version_status, role)
        flt = base.model_copy(update={
            "must": list(base.must or []) + [
                models.FieldCondition(
                    key="doc",  # MAI doc_stem: assente da molti punti
                    match=models.MatchAny(any=list(stems)),
                )
            ]
        })
        resp = qc.facet(self.collection, key="doc", facet_filter=flt,
                        limit=len(stems), exact=True)
        return {h.value for h in resp.hits if h.count > 0}

    def _reject_reason(
        self,
        hint: MemoryHint,
        versions: dict[str, tuple[str, datetime | None]],
        visible: set[str],
    ) -> str | None:
        """Primo motivo di scarto, o None. AND su TUTTI gli stem dell'hint."""
        for stem, _page in hint.evidence_ids:
            row = versions.get(stem)
            if row is None:
                return REASON_DOC_UNKNOWN
            status, ingested_at = row
            if status != self.version_status:
                return REASON_DOC_NOT_CURRENT
            if _is_stale(ingested_at, hint.created_at):
                return REASON_STALE
            if stem not in visible:
                return REASON_RBAC
        return None


def _is_stale(ingested_at: datetime | None,
              created_at: datetime | None) -> bool:
    """True se non si puo' PROVARE che la conferma e' posteriore all'ingestione.

    Fail-closed anche sull'incomparabile (naive vs aware, None): non e' un
    caso da indovinare, e' un caso in cui la prova manca.
    """
    try:
        return ingested_at > created_at  # type: ignore[operator]
    except TypeError:
        return True


def recall_validated(
    conn: psycopg.Connection,
    qc,
    *,
    role: Role,
    utente: str,
    query: str | None = None,
    limit: int = 5,
) -> list[MemoryHint]:
    """`MemoryStore.recall` + `RevalidationGate.validate`: solo i sopravvissuti.

    E' la SOLA funzione che un consumatore (la card di F.2c/F.2d, o F.3)
    dovrebbe chiamare: usare `recall` da solo significa mostrare binding non
    revalidati, cioe' il leak che questo modulo esiste per impedire.

    NOTA: oggi non ha consumatori. E' voluto — nessun percorso di produzione la
    chiama ancora.
    """
    if not isinstance(role, Role):
        raise TypeError(f"role deve essere Role, non {type(role).__name__}")
    try:
        hints = MemoryStore().recall(conn, role=role, utente=utente,
                                     query=query, limit=limit)
    except Exception as exc:
        # Stessa promessa del gate: la memoria non rompe mai una risposta.
        log.warning("recall della memoria fallito (%s: %s): nessun hint",
                    type(exc).__name__, exc)
        return []
    kept, _dropped = RevalidationGate().validate(hints, conn=conn, qc=qc,
                                                 role=role)
    return kept
