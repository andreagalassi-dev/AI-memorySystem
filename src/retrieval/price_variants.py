from dataclasses import dataclass
from typing import Any

from src.graph.tools.sql_tool import LookupRow

#: Vocabolario degli stati di versione, allineato al CHECK di
#: `document_versions` (src/registry/schema.sql) e alle costanti di
#: `src/registry/registry.py`. Ripetuti qui e non importati per non legare il
#: retrieval al modulo di scrittura del registro (che apre psycopg).
SUPERSEDED = "superseded"
DELETED = "deleted"


def normalize_version_status(raw: Any) -> str | None:
    """UNICO punto di normalizzazione dello stato di versione del progetto.

    Normalizza SOLO maiuscole e spazi: `"DELETED"`, `" Deleted "` sono lo stesso
    stato, e non riconoscerli significherebbe servire un documento eliminato
    senza avviso. Da Postgres questo non puo' succedere (c'e' un CHECK), ma il
    payload Qdrant e gli script di manutenzione non hanno lo stesso vincolo.

    NON fa match per prefisso ne' fuzzy: `"delete"`, `"deleted_at"`,
    `"obsolete"` NON sono stati di questo sistema e restano ignoti. Indovinare
    su un token che nessuno dei nostri scrittori produce vorrebbe dire inventare
    uno stato — l'opposto del principio del prodotto. NULL/stringa vuota e'
    l'assenza di informazione (LEFT JOIN su `document_versions`), non uno stato:
    ritorna None, e a valle non accende alcun avviso.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    return s or None


@dataclass(frozen=True)
class PriceVariant:
    prezzo: Any
    pagine: list[int]
    doc_stems: list[str]
    #: il valore e' attestato SOLO da documenti SOSTITUITI (cap MEDIA a valle)
    superseded: bool
    freq: int
    #: il valore e' attestato SOLO da documenti ELIMINATI (cap BASSA a valle).
    #: Campo SEPARATO da `superseded` di proposito: sono due avvisi diversi con
    #: due cap di livello diversi (`reconcile`), e un solo booleano costringerebbe
    #: a declassare un eliminato ad avviso di sostituito. Ultimo, con default,
    #: per non rompere le costruzioni posizionali gia' esistenti.
    deleted: bool = False


@dataclass(frozen=True)
class PriceAnswer:
    principale: PriceVariant
    varianti: list[PriceVariant]


def aggregate_price_variants(rows: list[LookupRow]) -> PriceAnswer:
    """Aggrega le varianti di prezzo e determina il principale.
    
    RANKING per eleggere il principale:
    1. NON-obsoleto prima: vivo (0) < solo-sostituito (1) < solo-eliminato (2)
    2. freq desc (count of rows for this price)
    3. numero pagine desc
    4. max(ingested_at) desc (con fallback per None)

    Con zero righe obsolete (l'intero corpus di oggi) la chiave 1 vale 0 per
    tutte le varianti e l'ordinamento e' identico a quello precedente.
    """
    if not rows:
        raise ValueError("Cannot aggregate empty rows")

    # Group by value
    groups: dict[Any, list[LookupRow]] = {}
    for r in rows:
        if r.valore not in groups:
            groups[r.valore] = []
        groups[r.valore].append(r)

    variants: list[PriceVariant] = []
    max_ingested = {}
    for val, group in groups.items():
        # Regola di GRUPPO: un VALORE di prezzo e' obsoleto solo se TUTTE le
        # righe che lo attestano lo sono. Se anche una fonte `current` riporta
        # lo stesso prezzo, il prezzo e' confermato da qualcosa di vivo e non va
        # declassato. (L'avviso all'utente non dipende da qui: scatta a livello
        # di RISPOSTA, con regola ANY, in `sql_node` — vedi il commento la'.)
        # NULL/ignoto NON e' obsoleto, come prima.
        stati = {normalize_version_status(r.version_status) for r in group}
        tutto_obsoleto = stati <= {SUPERSEDED, DELETED}
        # Nel gruppo tutto-obsoleto ma MISTO vince il piu' grave: un eliminato
        # non puo' essere declassato ad avviso di sostituito (cap BASSA vs MEDIA).
        deleted = tutto_obsoleto and DELETED in stati
        superseded = tutto_obsoleto and not deleted

        pagine = sorted(list(set(r.page for r in group if r.page is not None)))
        
        # Maintain order of doc_stems, remove duplicates
        doc_stems = []
        for r in group:
            if r.doc_stem and r.doc_stem not in doc_stems:
                doc_stems.append(r.doc_stem)

        # Fallback for ingested_at comparison (None as minimal value)
        max_ingested_at = max((r.ingested_at for r in group if r.ingested_at is not None), default=None)
        
        variants.append(PriceVariant(
            prezzo=val,
            pagine=pagine,
            doc_stems=doc_stems,
            superseded=superseded,
            freq=len(group),
            deleted=deleted
        ))
        max_ingested[val] = max_ingested_at

    def _rank(v: PriceVariant) -> int:
        """0 = vivo, 1 = solo sostituito, 2 = solo eliminato. Con nessuna riga
        obsoleta vale 0 ovunque: ordinamento invariato rispetto a prima."""
        if v.deleted:
            return 2
        return 1 if v.superseded else 0

    class SortKey:
        def __init__(self, v):
            self.v = v
            self.rank = _rank(v)
            self.ingested = max_ingested[self.v.prezzo]

        def __lt__(self, other):
            if self.rank != other.rank:
                return self.rank < other.rank  # vivo prima dell'obsoleto
            if self.v.freq != other.v.freq:
                return self.v.freq > other.v.freq # desc
            if len(self.v.pagine) != len(other.v.pagine):
                return len(self.v.pagine) > len(other.v.pagine) # desc
            
            # max_ingested desc comparison, with None as oldest
            if self.ingested != other.ingested:
                if self.ingested is None:
                    return False
                if other.ingested is None:
                    return True
                return self.ingested > other.ingested # desc
            
            # Final tie break: deterministic sort by string representation of price
            return str(self.v.prezzo) < str(other.v.prezzo)

    variants.sort(key=SortKey)

    return PriceAnswer(
        principale=variants[0],
        varianti=variants[1:]
    )
