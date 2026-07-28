import json
import fitz
from decimal import Decimal
import sys

sys.path.append(".")

from src.ingestion.extraction.extractor import PageRef
from src.ingestion.extraction.docling_extractor import DoclingTableExtractor
from src.ingestion.codes import canonical

class FakeConverter:
    def __init__(self, grid_data):
        self.grid_data = grid_data
        
    def convert(self, source):
        class DummyTableData:
            def __init__(self, grid):
                class Cell:
                    def __init__(self, text):
                        self.text = text
                self.grid = []
                for row in grid:
                    r = []
                    for c in row:
                        r.append(Cell(c))
                    self.grid.append(r)
        
        class DummyTable:
            def __init__(self, grid):
                self.data = DummyTableData(grid)
                
        class DummyDoc:
            def __init__(self, grid):
                self.tables = [DummyTable(grid)]
                
        class DummyResult:
            def __init__(self, grid):
                self.document = DummyDoc(grid)
                
        return DummyResult(self.grid_data)

def evaluate():
    with open("output/spike_b/goldenset/reviewer_truth.json", "r") as f:
        truth = json.load(f)
        
    correct = 0
    total = len(truth)
    
    print(f"{'Code':<20} | {'True Prc':<10} | {'Map Prc':<10} | {'Status':<10}")
    print("-" * 55)
    
    doc_fitz = fitz.Document()
    for _ in range(500):
        doc_fitz.new_page()
        
    for item in truth["items"]:
        doc = item["doc"]
        page = item["page"]
        code = item["code"]
        true_prezzo = Decimal(str(item["true_prezzo"])) if item.get("true_prezzo") is not None else None
        
        grid_path = f"tests/fixtures/docling_grids/{doc}_p{page}.json"
        
        try:
            with open(grid_path, "r") as f:
                grid = json.load(f)
        except FileNotFoundError:
            print(f"{code:<20} | {str(true_prezzo):<10} | {'N/A':<10} | MISS_FIXTURE")
            continue
            
        converter = FakeConverter(grid)
        extractor = DoclingTableExtractor(doc_fitz, converter=converter)
        ref = PageRef(doc_stem=doc, page=page)
        
        mapped_rows = extractor.extract(ref)
        
        found = False
        for r in mapped_rows:
            if canonical(r.codice_originale) == canonical(code):
                found = True
                map_prc = r.prezzo
                
                if map_prc == true_prezzo:
                    print(f"{code:<20} | {str(true_prezzo):<10} | {str(map_prc):<10} | CORRECT")
                    correct += 1
                else:
                    print(f"{code:<20} | {str(true_prezzo):<10} | {str(map_prc):<10} | WRONG_PRICE")
                break
                
        if not found:
            print(f"{code:<20} | {str(true_prezzo):<10} | {'None':<10} | MISS_ROW")
            
    print(f"\nTotal Correct: {correct}/{total}")

if __name__ == "__main__":
    evaluate()
