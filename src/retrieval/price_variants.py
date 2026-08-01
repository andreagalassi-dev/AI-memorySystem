from dataclasses import dataclass
from typing import Any

from src.graph.tools.sql_tool import LookupRow


@dataclass(frozen=True)
class PriceVariant:
    prezzo: Any
    pagine: list[int]
    doc_stems: list[str]
    superseded: bool
    freq: int


@dataclass(frozen=True)
class PriceAnswer:
    principale: PriceVariant
    varianti: list[PriceVariant]


def aggregate_price_variants(rows: list[LookupRow]) -> PriceAnswer:
    """Aggrega le varianti di prezzo e determina il principale.
    
    RANKING per eleggere il principale:
    1. NON-superseded prima [False < True] (i.e. superseded ascending)
    2. freq desc (count of rows for this price)
    3. numero pagine desc
    4. max(ingested_at) desc (con fallback per None)
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
        # A variant is superseded ONLY if all its rows are superseded
        # If version_status is NULL, it is NOT superseded.
        superseded = all(r.version_status == "superseded" for r in group)
        
        pagine = sorted(list(set(r.page for r in group if r.page is not None)))
        
        # Maintain order of doc_stems, remove duplicates
        doc_stems = []
        for r in group:
            if r.doc_stem and r.doc_stem not in doc_stems:
                doc_stems.append(r.doc_stem)

        # Fallback for ingested_at comparison (empty string as minimal value)
        max_ingested_at = max((r.ingested_at for r in group if r.ingested_at is not None), default="")
        
        variants.append(PriceVariant(
            prezzo=val,
            pagine=pagine,
            doc_stems=doc_stems,
            superseded=superseded,
            freq=len(group)
        ))
        max_ingested[val] = max_ingested_at

    class SortKey:
        def __init__(self, v):
            self.v = v
        
        def __lt__(self, other):
            if self.v.superseded != other.v.superseded:
                return self.v.superseded < other.v.superseded # False < True
            if self.v.freq != other.v.freq:
                return self.v.freq > other.v.freq # desc
            if len(self.v.pagine) != len(other.v.pagine):
                return len(self.v.pagine) > len(other.v.pagine) # desc
            # max_ingested string comparison
            self_ingested = max_ingested[self.v.prezzo]
            other_ingested = max_ingested[other.v.prezzo]
            return self_ingested > other_ingested # desc

    variants.sort(key=SortKey)

    return PriceAnswer(
        principale=variants[0],
        varianti=variants[1:]
    )
