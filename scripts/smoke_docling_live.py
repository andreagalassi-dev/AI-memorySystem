import fitz
import time
from decimal import Decimal

from src.ingestion.extraction.extractor import PageRef
from src.ingestion.extraction.docling_extractor import DoclingTableExtractor

def smoke_test():
    doc_path = "corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf"
    doc = fitz.open(doc_path)
    
    print("Initializing DoclingTableExtractor (loading models)...")
    t0 = time.time()
    # We do NOT pass a fake converter, so it uses the real Docling DocumentConverter
    extractor = DoclingTableExtractor(doc)
    print(f"Initialized in {time.time()-t0:.2f}s")
    
    ref = PageRef(doc_stem="Mamoli", page=225)
    print(f"Extracting page 225...")
    
    t1 = time.time()
    righe = extractor.extract(ref)
    print(f"Extracted in {time.time()-t1:.2f}s")
    
    print(f"Total rows extracted: {len(righe)}")
    assert len(righe) >= 100, f"Expected >= 100 rows, got {len(righe)}"
    
    found_target = False
    for r in righe:
        if r.codice_originale == "00000029T007":
            found_target = True
            print(f"Found target: {r}")
            assert r.prezzo == Decimal("58.00"), f"Expected 58.00, got {r.prezzo}"
            
    assert found_target, "Target code 00000029T007 not found!"
    print("SMOKE TEST PASSED.")

if __name__ == "__main__":
    smoke_test()
