import os
import tempfile
import fitz
from typing import Optional

from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

from src.ingestion.extraction.extractor import PageRef
from src.ingestion.extraction.schema import RigaGrezza
from src.ingestion.extraction.docling_mapper import DoclingMapper, DOCLING_CODE_RE

class DoclingTableExtractor:
    def __init__(self, doc: fitz.Document, *, converter: Optional[DocumentConverter] = None):
        self._doc = doc
        self.rejects = []
        self.mapper = DoclingMapper()
        
        if converter is None:
            pipeline_options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
            pipeline_options.table_structure_options.mode = "accurate"
            self.converter = DocumentConverter(
                format_options={
                    InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
                }
            )
        else:
            self.converter = converter
            
    def extract(self, ref: PageRef) -> list[RigaGrezza]:
        self.rejects.clear()
        
        # 1. Isolate the page in a temp PDF
        # ref.page is 1-based, fitz uses 0-based
        page_idx = ref.page - 1
        
        temp_doc = fitz.Document()
        temp_doc.insert_pdf(self._doc, from_page=page_idx, to_page=page_idx)
        
        fd, temp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        
        try:
            temp_doc.save(temp_path)
            temp_doc.close()
            
            # 2. Convert using Docling
            result = self.converter.convert(temp_path)
            
            # 3. Process tables
            out_rows = []
            
            for table in result.document.tables:
                grid = []
                for row_data in table.data.grid:
                    # row_data is a list of cells
                    grid_row = [cell.text for cell in row_data]
                    grid.append(grid_row)
                    
                    # Popola rejects: righe senza un codice mappato
                    # We check if any cell contains a code according to DOCLING_CODE_RE
                    has_code = False
                    for cell_text in grid_row:
                        t = cell_text.strip()
                        t_nospace = t.replace(" ", "")
                        if DOCLING_CODE_RE.match(t) or DOCLING_CODE_RE.match(t_nospace):
                            has_code = True
                            break
                        # We should also check the prefix logic used in the mapper
                        tokens = t.split()
                        for j in range(1, len(tokens)):
                            prefix_t = " ".join(tokens[:j])
                            prefix_nospace = prefix_t.replace(" ", "")
                            if DOCLING_CODE_RE.match(prefix_t) or DOCLING_CODE_RE.match(prefix_nospace):
                                has_code = True
                                break
                        if has_code:
                            break
                            
                    if not has_code:
                        self.rejects.append(grid_row)
                
                # Pass grid to mapper
                mapped_rows = self.mapper.map_page(grid, doc_stem=ref.doc_stem, page=ref.page)
                out_rows.extend(mapped_rows)
                
            # 4. Dedup across the page by (doc, page, codice) keeping the last occurrence
            final_out = {}
            for r in out_rows:
                final_out[r.codice_originale] = r
                
            return list(final_out.values())
            
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
