import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.ingestion.fast_dispatch import dispatch_pdf

def main():
    doc_stem = "Mamoli_Catalogo_Ricambi_2021_IT_EN"
    pdf_path = Path("corpus/original_dataset") / f"{doc_stem}.pdf"
    
    dispatch = dispatch_pdf(str(pdf_path), extract_text=False)
    for p in [154, 155, 156, 185, 186, 200, 230]:
        page_dispatch = dispatch.pages[p - 1]
        print(f"Page {p}: route={page_dispatch.route.value}, reason={page_dispatch.reason}, features={page_dispatch.features}")

if __name__ == '__main__':
    main()
