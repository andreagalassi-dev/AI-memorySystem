import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz
from src.ingestion.extraction.table_detect import is_table_page

def main():
    doc_stem = "Mamoli_Catalogo_Ricambi_2021_IT_EN"
    pdf_path = Path("corpus/original_dataset") / f"{doc_stem}.pdf"
    doc = fitz.open(pdf_path)
    
    for p in [154, 155, 156, 185, 186, 200, 230]:
        page = doc.load_page(p - 1)
        verdict = is_table_page(page)
        print(f"Page {p}: is_table={verdict.is_table}, reason={verdict.reason}")

if __name__ == '__main__':
    main()
