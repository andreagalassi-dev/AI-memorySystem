"""P4.3 — Riconciliatore Qdrant <- SSOT (proiezione convergente).

Ogni run legge la verita' (export del registro) e patcha SOLO gli stem
divergenti: rilanciabile alla cieca dopo un crash parziale — la parte gia'
fatta risulta allineata (0 patch), il resto converge. L'allineamento e'
verificato con count esatti per stem (robusto anche a patch parziali
DENTRO uno stem), non con un punto campione.
"""

from __future__ import annotations

from typing import NamedTuple

from qdrant_client import QdrantClient, models

from src.registry.registry import BackfillRecord, backfill_payload

NEW_KEYS = ("version_status", "doc_version", "ruoli_consentiti")


class SyncReport(NamedTuple):
    collection: str
    patched: list[str]
    skipped: int
    missing_stems: list[str]  # stems SSOT senza punti (es. v2 non ingerita)


def doc_filter(stem: str) -> models.Filter:
    return models.Filter(must=[models.FieldCondition(
        key="doc", match=models.MatchValue(value=stem))])


def facet_docs(qc: QdrantClient, coll: str) -> set[str]:
    r = qc.facet(coll, key="doc", limit=1000, exact=True)
    return {h.value for h in r.hits}


def alignment_filter(stem: str, rec: BackfillRecord) -> models.Filter:
    """Filtro "punto gia' allineato alla SSOT": chiavi attese in must,
    ruoli NON previsti in must_not (rileva anche ruoli da rimuovere)."""
    payload = backfill_payload(rec)
    must = [models.FieldCondition(key="doc", match=models.MatchValue(value=stem)),
            models.FieldCondition(key="version_status",
                                  match=models.MatchValue(value=payload["version_status"])),
            models.FieldCondition(key="doc_version",
                                  match=models.MatchValue(value=payload["doc_version"]))]
    must += [models.FieldCondition(key="ruoli_consentiti",
                                   match=models.MatchValue(value=r))
             for r in payload["ruoli_consentiti"]]
    from src.common.roles import Role
    absent = [r.value for r in Role if r.value not in payload["ruoli_consentiti"]]
    must_not = [models.FieldCondition(key="ruoli_consentiti",
                                      match=models.MatchValue(value=r))
                for r in absent]
    return models.Filter(must=must, must_not=must_not)


def stems_to_patch(counts: dict[str, tuple[int, int]]) -> list[str]:
    """Decisione pura: stem divergente se (punti allineati) != (punti totali)."""
    return sorted(stem for stem, (aligned, total) in counts.items()
                  if aligned != total)


def _count(qc: QdrantClient, coll: str, flt: models.Filter) -> int:
    return qc.count(coll, exact=True, count_filter=flt).count


def sync_collection(qc: QdrantClient, coll: str,
                    export: dict[str, BackfillRecord]) -> SyncReport:
    """Converge una collection allo stato SSOT. Idempotente e rilanciabile."""
    present = facet_docs(qc, coll)
    orphans = present - set(export)
    if orphans:
        raise ValueError(f"{coll}: doc stems fuori registro (la SSOT ha buchi): "
                         f"{sorted(orphans)[:5]}")
    counts = {stem: (_count(qc, coll, alignment_filter(stem, export[stem])),
                     _count(qc, coll, doc_filter(stem)))
              for stem in present}
    to_patch = stems_to_patch(counts)
    for stem in to_patch:
        qc.set_payload(coll, payload=backfill_payload(export[stem]),
                       points=doc_filter(stem), wait=True)
    return SyncReport(collection=coll, patched=to_patch,
                      skipped=len(present) - len(to_patch),
                      missing_stems=sorted(set(export) - present))
