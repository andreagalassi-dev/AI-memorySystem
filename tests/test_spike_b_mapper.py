import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from scripts.spike_b_docscale import Mapper, extract_docling_rows

def test_mapper_mamoli_p229():
    pdf_path = "corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf"
    
    # Extract grid for page 229
    grid = extract_docling_rows(pdf_path, 229)
    
    mapper = Mapper()
    mapped_count = 0
    
    for row_cells in grid:
        if hasattr(mapper, 'map_cells'):
            res = mapper.map_cells(row_cells)
            if res:
                mapped_count += len(res)
        else:
            res = mapper.map_row(row_cells)
            if res:
                mapped_count += 1
                
    assert mapped_count >= 140, f"Expected at least 140 mapped rows, but got {mapped_count}"
