"""P5.3/D1 — Valutazione della metrica R_hierarchy per il Two-Step Retrieval e estrazione BOM.

Aggiunge la misurazione del recall gerarchico. Intercetta le query di tipo 
assembly/kit e misura quanti figli attesi vengono effettivamente recuperati.
"""

from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.qdrant_client import get_qdrant_client
from src.retrieval.search import search_unified
from src.retrieval.bom import get_bom
from src.common.roles import Role

def calc_r_hierarchy(children_hits: list | set, expected_child_codes: list[str]) -> float:
    """Calcola la metrica R_hierarchy.
    R_hierarchy = (numero di figli attesi trovati) / (numero totale di figli attesi)
    """
    if not expected_child_codes:
        return 1.0
    
    if isinstance(children_hits, set):
        retrieved_codes = children_hits
    else:
        retrieved_codes = {h.payload.get("code") for h in children_hits if h.payload.get("code")}
        
    expected_set = set(expected_child_codes)
    
    matched = retrieved_codes.intersection(expected_set)
    return len(matched) / len(expected_set)

def load_eval_set(filepath: Path) -> list[dict]:
    eval_set = []
    if filepath.exists():
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    eval_set.append(json.loads(line))
    return eval_set

def main() -> int:
    parser = argparse.ArgumentParser(description="Valuta la metrica R_hierarchy.")
    parser.add_argument("--eval-file", default="data/eval_set_v1.jsonl", help="Percorso del file eval_set (JSONL)")
    parser.add_argument("--use-bom", action="store_true", help="Se usare get_bom() invece di search_unified() (test D1)")
    args = parser.parse_args()
    
    try:
        client = get_qdrant_client()
    except Exception as e:
        print(f"[eval_hierarchy] Errore connessione Qdrant: {e}")
        return 1
        
    print("[eval_hierarchy] Inizializzazione calcolo R_hierarchy...")
    
    eval_file = ROOT / args.eval_file
    eval_set = load_eval_set(eval_file)
    
    # Se il file non esiste o non ha record con expected_children, usiamo un mock realistico come fallback
    has_hierarchy = any("expected_children" in item for item in eval_set)
    
    if not eval_set or not has_hierarchy:
        print(f"[eval_hierarchy] Nessun campo 'expected_children' trovato in {args.eval_file}. Uso mock di fallback.")
        eval_set = [
            {
                "qa_id": "H001",
                "query": "kit frizione", # fittizia
                "kit_code": "KIT-123",   # necessario se usiamo --use-bom
                "query_type": "natural",
                "expected_children": ["CHILD001", "CHILD002"] 
            }
        ]
    
    total_r_hierarchy = 0.0
    count = 0
    
    for pair in eval_set:
        if "expected_children" in pair:
            if args.use_bom:
                kit_code = pair.get("kit_code")
                if not kit_code:
                    print(f"[{pair.get('qa_id', '???')}] Skipped: Nessun 'kit_code' definito.")
                    continue
                bom_items = get_bom(client, kit_code, Role.TECNICO)
                retrieved_codes = {item["code"] for item in bom_items}
                r_hier = calc_r_hierarchy(retrieved_codes, pair["expected_children"])
                total_found = len(retrieved_codes)
            else:
                result = search_unified(client, pair["query"], limit=10, role=Role.TECNICO)
                children_hits = result.get("children_hits", [])
                r_hier = calc_r_hierarchy(children_hits, pair["expected_children"])
                total_found = len(children_hits)
                
            total_r_hierarchy += r_hier
            count += 1
            print(f"[{pair.get('qa_id', '???')}] R_hierarchy = {r_hier:.2f} "
                  f"(Trovati: {total_found}, Attesi: {len(pair['expected_children'])})")
            
    if count > 0:
        avg = total_r_hierarchy / count
        print(f"\n[eval_hierarchy] Media R_hierarchy su {count} query gerarchiche: {avg:.2f}")
    else:
        print("[eval_hierarchy] Nessuna query valida valutata.")
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
