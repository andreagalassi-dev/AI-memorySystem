import json
import re
from decimal import Decimal
from src.ingestion.extraction.docling_mapper import DoclingMapper
from src.ingestion.codes import canonical

def evaluate():
    with open("output/spike_b/goldenset/reviewer_truth.json") as f:
        truth = json.load(f)["items"]
        
    mapper = DoclingMapper()
    
    # We only have grids for specific pages. We generated them all.
    # Check them.
    correct_count = 0
    total = len(truth)
    
    print(f"{'Code':<20} | {'True Prc':<10} | {'Map Prc':<10} | {'Status':<10}")
    print("-" * 55)
    
    for item in truth:
        doc = item["doc"]
        if doc == "kohler":
            doc = "kohler_marine" # Fix doc name mismatch
        page = item["page"]
        code = item["code"]
        true_prezzo = Decimal(str(item["true_prezzo"])) if item["true_prezzo"] is not None else None
        
        try:
            with open(f"tests/fixtures/docling_grids/{doc}_p{page}.json") as f:
                grid = json.load(f)
        except FileNotFoundError:
            print(f"{code:<20} | {str(true_prezzo):<10} | {'N/A':<10} | MISSING FIXTURE")
            continue
            
        mapped_rows = mapper.map_page(grid, doc_stem=doc, page=page)
        
        # Match using normalized code
        target_norm = canonical(code)
        found_row = None
        for r in mapped_rows:
            if canonical(r.codice_originale) == target_norm:
                found_row = r
                break
                
        if found_row is None:
            print(f"{code:<20} | {str(true_prezzo):<10} | {'None':<10} | MISS_ROW")
        else:
            map_prc = found_row.prezzo
            if map_prc == true_prezzo:
                print(f"{code:<20} | {str(true_prezzo):<10} | {str(map_prc):<10} | CORRECT")
                correct_count += 1
            else:
                print(f"{code:<20} | {str(true_prezzo):<10} | {str(map_prc):<10} | WRONG_PRICE")
                
    print(f"\nTotal Correct: {correct_count}/{total}")

if __name__ == "__main__":
    evaluate()
