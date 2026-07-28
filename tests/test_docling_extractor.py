import json
import fitz
import pytest
from decimal import Decimal
from src.ingestion.extraction.extractor import PageRef

class FakeConverter:
    def __init__(self, grid_json_path):
        with open(grid_json_path, 'r') as f:
            self.grid_data = json.load(f)
            
    def convert(self, source):
        # We return a dummy object that mimics the docling document structure
        # for a single table with our grid data
        class DummyTableData:
            def __init__(self, grid):
                # docling table.data.grid[r][c].text
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


def test_docling_extractor_mamoli_p225():
    # Arrange
    # Use a dummy fitz doc, not used by FakeConverter but required by init
    doc = fitz.Document() 
    for _ in range(225):
        doc.new_page()
    converter = FakeConverter("tests/fixtures/docling_grids/Mamoli_p225.json")
    
    # We expect this import to fail initially (Red step)
    from src.ingestion.extraction.docling_extractor import DoclingTableExtractor
    
    extractor = DoclingTableExtractor(doc, converter=converter)
    ref = PageRef(doc_stem="Mamoli", page=225)
    
    # Act
    righe = extractor.extract(ref)
    
    # Assert
    # Check that it extracted the target item: 00000029T007 -> 58.00
    target_found = False
    for r in righe:
        if r.codice_originale == "00000029T007":
            target_found = True
            assert r.prezzo == Decimal("58.00")
            
    assert target_found, "Il codice 00000029T007 non e' stato trovato nella pagina"
