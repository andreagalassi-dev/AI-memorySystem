import sys
import os
import json
import fitz
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

PDF_MAP = {
    "Mamoli": "corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf",
    "nsk": "corpus/price_lists/nsk_cuscinetti_listino_2025.pdf",
    "kohler_marine": "corpus/engines/kohler_marine_tp6954.pdf",
    "ivm": "corpus/hardware_fasteners/ivm_edilizia_catalistino_2026.pdf",
}

# Added the extra pages required by the test for coverage in reviewer_truth
PAGES_TO_EXTRACT = {
    "Mamoli": [225, 224, 231, 189, 92, 229, 131, 138],
    "nsk": [27, 228, 135, 63, 39, 33],
    "kohler_marine": [37, 95, 33, 74],
    "ivm": [172, 85, 218, 168]
}

def extract_docling_rows(converter, pdf_path, page_no):
    temp_pdf = f"temp_page_{page_no}.pdf"
    
    doc_fitz = fitz.open(pdf_path)
    doc_single = fitz.open()
    doc_single.insert_pdf(doc_fitz, from_page=page_no-1, to_page=page_no-1)
    doc_single.save(temp_pdf)
    doc_single.close()
    doc_fitz.close()
    
    import logging
    logging.getLogger("docling").setLevel(logging.ERROR)
    
    res = converter.convert(temp_pdf)
    doc = res.document
    
    tables = doc.tables
    
    rows = []
    for t in tables:
        for r_idx in range(t.data.num_rows):
            row_cells = []
            for c_idx in range(t.data.num_cols):
                try:
                    cell = t.data.grid[r_idx][c_idx]
                    row_cells.append(cell.text if cell.text else "")
                except IndexError:
                    row_cells.append("")
            rows.append(row_cells)
            
    if os.path.exists(temp_pdf):
        os.remove(temp_pdf)
        
    return rows

def main():
    os.makedirs("tests/fixtures/docling_grids", exist_ok=True)
    
    pipeline_options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    pipeline_options.table_structure_options.mode = "accurate"
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )
    
    for doc_key, pages in PAGES_TO_EXTRACT.items():
        pdf_path = PDF_MAP[doc_key]
        for page in pages:
            print(f"Extracting {doc_key} page {page}...")
            grid = extract_docling_rows(converter, pdf_path, page)
            out_file = f"tests/fixtures/docling_grids/{doc_key}_p{page}.json"
            with open(out_file, "w") as f:
                json.dump(grid, f, indent=2)

if __name__ == "__main__":
    main()
