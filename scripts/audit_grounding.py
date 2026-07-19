import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from src.ingestion.extraction.text_table import TextTableExtractor
from src.ingestion.extraction.extractor import PageRef
from src.ingestion.extraction.grounding import partition
from src.ingestion.extraction.schema import RigaListino

def main():
    doc_stem = "Mamoli_Catalogo_Ricambi_2021_IT_EN"
    pdf_path = Path("corpus/original_dataset") / f"{doc_stem}.pdf"
    doc = fitz.open(pdf_path)
    extractor = TextTableExtractor(doc)
    
    for p in [154, 155, 156, 185, 186, 200, 230]:
        try:
            page_obj = doc.load_page(p - 1)
            page_text = page_obj.get_text("text")
            
            ref = PageRef(doc_stem, p)
            grezze = extractor.extract(ref)
            righe = [RigaListino.from_grezza(g, doc_stem=doc_stem, page=p) for g in grezze]
            
            valide, scarti = partition(righe, page_text)
            print(f"Page {p}: {len(grezze)} extracted -> {len(valide)} valide, {len(scarti)} scarti")
        except Exception as e:
            print(f"Page {p}: Error {e}")

if __name__ == '__main__':
    main()
