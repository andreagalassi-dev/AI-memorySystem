import pytest
import json
import os
from decimal import Decimal
from src.ingestion.extraction.schema import RigaGrezza
from src.ingestion.extraction.docling_mapper import DoclingMapper

def load_grid(doc_stem: str, page: int) -> list[list[str]]:
    path = f"tests/fixtures/docling_grids/{doc_stem}_p{page}.json"
    with open(path, "r") as f:
        return json.load(f)

def test_mapper_mamoli_p225_00000029T007():
    grid = load_grid("Mamoli", 225)
    mapper = DoclingMapper()
    
    mapped = mapper.map_page(grid, doc_stem="Mamoli", page=225)
    
    # Check that 00000029T007 is found and price is 58.00, not 85
    found = None
    for r in mapped:
        if r.codice_originale == "00000029T007":
            found = r
            break
            
    assert found is not None, "Code 00000029T007 not found"
    assert found.prezzo == Decimal("58.00")
