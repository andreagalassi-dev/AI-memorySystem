# src/eval/plumbing.py
from typing import Any
from src.eval.truth_set import EvalItem

def build_plumbing_items(rows: list[tuple[str, int | None, str, str, float | None]]) -> list[EvalItem]:
    """Costruisce gli item 'plumbing' per la rete anti-regressione.
    
    Args:
        rows: Lista di tuple (doc_stem, page, codice_normalizzato, descrizione, prezzo)
    """
    items = []
    for doc, page, code, desc, price in rows:
        parts = [code, desc]
        if price is not None:
            # Rimuove zero non necessari se è un float tondo come 100.0, per il test abbiamo 100.5
            parts.append(str(price).rstrip('0').rstrip('.') if '.' in str(price) else str(price))
        expected = " ".join(p for p in parts if p)
        item = EvalItem(
            id=f"plumb_{doc}_{code}",
            query=code,
            lang="it",
            branch="structured",
            role="tecnico",
            doc=doc,
            page=page,
            expected=expected,
            kind="positive",
            must_say_nonso=False,
            source="plumbing"
        )
        items.append(item)
    return items

def build_coverage_row(doc_stem: str, structured: set[str], narrative: set[str], visual: set[str]) -> dict[str, Any]:
    """Genera la riga di coverage per un PDF."""
    has_struct = doc_stem in structured
    has_narrative = doc_stem in narrative
    has_visual = doc_stem in visual
    return {
        "doc": doc_stem,
        "structured": has_struct,
        "narrative": has_narrative,
        "visual": has_visual,
        "indexed": has_struct or has_narrative or has_visual
    }
