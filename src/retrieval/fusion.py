"""P3.3 — Fusion testo↔visione: segnale di accordo (A).

Matching deterministico su tuple (doc, page): le due collection condividono lo
STESSO stem `doc` e una `page` intera (aike_chunks e aike_pages_visual), quindi
l'accordo e' un confronto diretto, nessuna normalizzazione. Design: docs/
superpowers/specs/2026-07-04-p33-fusion-agreement-design.md.
"""

from __future__ import annotations

from typing import Sequence


def page_agreement(
    text: tuple[str, int] | None,
    visual: Sequence[tuple[str, int]],
    *,
    delta: int,
    topk: int = 3,
) -> str | None:
    """Accordo tra il top-1 testuale `(doc,page)` e i top visivi.

    - "agree"    : un hit nei primi `topk` visivi ha stesso doc e |page diff|<=delta;
    - "disagree" : niente agree e il top-1 visivo ha doc diverso (conflitto);
    - None        : canale vuoto, o stesso doc ma pagine lontane (segnale debole).
    """
    if text is None or not visual:
        return None
    doc_t, page_t = text
    cand = list(visual[:topk])
    for doc_v, page_v in cand:
        if doc_v == doc_t and abs(page_v - page_t) <= delta:
            return "agree"
    if cand and cand[0][0] != doc_t:
        return "disagree"
    return None


def hit_docpage(hit) -> tuple[str, int] | None:
    """Estrae (doc, page) dal payload di un hit Qdrant; None se assenti."""
    pl = getattr(hit, "payload", None) or {}
    doc, page = pl.get("doc"), pl.get("page")
    return (doc, page) if doc is not None and page is not None else None
