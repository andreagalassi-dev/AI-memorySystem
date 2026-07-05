"""P5.3 — Valutazione della metrica R_hierarchy per il Two-Step Retrieval.

Aggiunge la misurazione del recall gerarchico. Intercetta le query di tipo 
assembly/kit e misura quanti figli attesi vengono effettivamente recuperati.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.qdrant_client import get_qdrant_client
from src.retrieval.search import search_unified

def calc_r_hierarchy(children_hits: list, expected_child_codes: list[str]) -> float:
    """Calcola la metrica R_hierarchy.
    R_hierarchy = (numero di figli attesi trovati) / (numero totale di figli attesi)
    """
    if not expected_child_codes:
        return 1.0
    
    retrieved_codes = {h.payload.get("code") for h in children_hits if h.payload.get("code")}
    expected_set = set(expected_child_codes)
    
    matched = retrieved_codes.intersection(expected_set)
    return len(matched) / len(expected_set)

def main() -> int:
    client = get_qdrant_client()
    print("[eval_hierarchy] Inizializzazione calcolo R_hierarchy...")
    
    # Per supportare R_hierarchy nell'eval_set, ci aspettiamo un campo `expected_children`
    # Esempio di dato mockato per dimostrare il funzionamento:
    mock_eval_set = [
        {
            "qa_id": "H001",
            "query": "kit frizione", # fittizia
            "query_type": "natural",
            "expected_children": ["CHILD001", "CHILD002"] 
        }
    ]
    
    total_r_hierarchy = 0.0
    count = 0
    
    # Simula iterazione sull'eval set (nella realta' caricherebbe eval_set_v1.jsonl)
    for pair in mock_eval_set:
        if "expected_children" in pair:
            result = search_unified(client, pair["query"], limit=10)
            children_hits = result.get("children_hits", [])
            r_hier = calc_r_hierarchy(children_hits, pair["expected_children"])
            total_r_hierarchy += r_hier
            count += 1
            print(f"[{pair['qa_id']}] R_hierarchy = {r_hier:.2f} "
                  f"(Trovati: {len(children_hits)}, Attesi: {len(pair['expected_children'])})")
            
    if count > 0:
        avg = total_r_hierarchy / count
        print(f"\n[eval_hierarchy] Media R_hierarchy su {count} query gerarchiche: {avg:.2f}")
    else:
        print("[eval_hierarchy] Nessuna query con 'expected_children' trovata nell'eval set.")
        print("Per testare il sistema, arricchire l'eval_set.jsonl con il campo 'expected_children'.")
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
