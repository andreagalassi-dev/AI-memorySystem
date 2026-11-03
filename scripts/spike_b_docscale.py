import sys
import json
import re
import fitz
import os
import psycopg
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions

from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from src.ingestion.extraction.text_table import DEFAULT_CODE_RE, _to_decimal
from src.ingestion.codes import base_cleanup
from src.ingestion.extraction.quality_guard import is_spillover_row
from src.ingestion.extraction.schema import RigaGrezza, RigaListino
from src.ingestion.extraction.load import init_schema, upsert_righe
from src.backend.clients.postgres_client import conninfo

def extract_docling_rows(converter, pdf_path, page_no):
    temp_pdf = f"output/spike_b/docscale/temp_page_{page_no}.pdf"
    os.makedirs("output/spike_b/docscale", exist_ok=True)
    
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

class Mapper:
    def __init__(self):
        self.code_re = DEFAULT_CODE_RE
        
    def map_cells(self, row_cells):
        records = []
        current_record = None
        
        for cell_text in row_cells:
            t = cell_text.strip()
            if not t:
                continue
                
            t_nospaces = t.replace(" ", "")
            code_match = self.code_re.match(t) or self.code_re.match(t_nospaces)
            
            if code_match:
                if current_record:
                    records.append(current_record)
                    
                code_str = code_match.group()
                current_record = {
                    "codice_originale": code_str,
                    "desc_cells": [],
                    "prezzo": None
                }
            elif current_record:
                clean_t = t.replace('€', '').replace('$', '').strip()
                tokens = clean_t.split()
                if tokens:
                    last_token = tokens[-1]
                    p = _to_decimal(last_token)
                    if p is not None:
                        current_record["prezzo"] = p
                        if len(tokens) > 1:
                            remaining_desc = " ".join(tokens[:-1])
                            if re.search(r'[a-zA-Z]{3,}', remaining_desc):
                                current_record["desc_cells"].append(remaining_desc)
                    else:
                        if re.search(r'[a-zA-Z]{3,}', t):
                            current_record["desc_cells"].append(t)
                else:
                    if re.search(r'[a-zA-Z]{3,}', t):
                        current_record["desc_cells"].append(t)
                        
        if current_record:
            records.append(current_record)
            
        out_records = []
        for r in records:
            dedup_desc = []
            for d in r["desc_cells"]:
                if d not in dedup_desc:
                    dedup_desc.append(d)
                    
            desc = " ".join(dedup_desc)
            desc = base_cleanup(desc) if desc else None
            
            out_records.append({
                "codice_originale": r["codice_originale"],
                "descrizione": desc,
                "prezzo": r["prezzo"]
            })
            
        return out_records if out_records else None

def analyze_rows(rows):
    if not rows:
        return {
            "total": 0,
            "has_price_pct": 0.0,
            "has_real_desc_pct": 0.0,
            "soup_pct": 0.0,
            "duplicates": 0
        }
    
    total = len(rows)
    has_price = 0
    has_real_desc = 0
    soup = 0
    
    seen = set()
    duplicates = 0
    
    for r in rows:
        if r['prezzo'] is not None:
            has_price += 1
        
        desc = r.get('descrizione') or ""
        if re.search(r'[a-zA-Z]{3,}', desc):
            has_real_desc += 1
            
        if is_spillover_row(desc):
            soup += 1
            
        key = (r['doc_stem'], r['page'], r['codice_originale'])
        if key in seen:
            duplicates += 1
        seen.add(key)
        
    return {
        "total": total,
        "has_price_pct": round((has_price / total) * 100, 2),
        "has_real_desc_pct": round((has_real_desc / total) * 100, 2),
        "soup_pct": round((soup / total) * 100, 2),
        "duplicates": duplicates
    }

def main():
    target_docs = [
        "corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf",
        "corpus/hardware_fasteners/ivm_edilizia_catalistino_2026.pdf",
        "corpus/engines/kohler_marine_tp6954.pdf",
        "corpus/price_lists/nsk_cuscinetti_listino_2025.pdf"
    ]
    
    SCHEMA = "b_tmp"
    
    conn = psycopg.connect(conninfo())
    init_schema(conn, schema=SCHEMA)
    
    matrix = {}
    spot_raw = []
    
    mapper = Mapper()
    
    pipeline_options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    pipeline_options.table_structure_options.mode = "accurate"
    converter = DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
        }
    )
    
    for pdf_path in target_docs:
        doc_stem = os.path.basename(pdf_path).replace(".pdf", "")
        print(f"Processing {doc_stem}...")
        
        doc_fitz = fitz.open(pdf_path)
        total_pages = len(doc_fitz)
        doc_fitz.close()
        
        docling_righe = []
        
        for page_no in range(1, total_pages + 1):
            print(f"  Page {page_no}/{total_pages}", end="\r")
            grid = extract_docling_rows(converter, pdf_path, page_no)
            
            for row_cells in grid:
                mapped_list = mapper.map_cells(row_cells)
                if mapped_list:
                    for mapped in mapped_list:
                        try:
                            grezza = RigaGrezza(**mapped)
                            riga = RigaListino.from_grezza(grezza, doc_stem=doc_stem, page=page_no)
                            docling_righe.append(riga)
                        except Exception:
                            pass
                        
        print(f"\n  Found {len(docling_righe)} rows with Docling.")
        
        upsert_righe(conn, docling_righe, schema=SCHEMA)
        
        docling_dicts = []
        for r in docling_righe:
            docling_dicts.append({
                "doc_stem": r.doc_stem,
                "page": r.page,
                "codice_normalizzato": r.codice_normalizzato,
                "codice_originale": r.codice_originale,
                "descrizione": r.descrizione,
                "prezzo": r.prezzo
            })
            
        cur = conn.cursor()
        cur.execute("SELECT doc_stem, page, codice_normalizzato, codice_originale, descrizione, prezzo FROM estrazione.righe_listino WHERE doc_stem = %s", (doc_stem,))
        prod_dicts = []
        for row in cur.fetchall():
            prod_dicts.append({
                "doc_stem": row[0],
                "page": row[1],
                "codice_normalizzato": row[2],
                "codice_originale": row[3],
                "descrizione": row[4],
                "prezzo": row[5]
            })
            
        matrix[doc_stem] = {
            "docling": analyze_rows(docling_dicts),
            "produzione": analyze_rows(prod_dicts)
        }
        
        spot_count = 0
        for i, d in enumerate(docling_dicts):
            if spot_count >= 4:
                break
            match = next((p for p in prod_dicts if p['page'] == d['page'] and p['codice_originale'] == d['codice_originale']), None)
            spot_raw.append({
                "doc": doc_stem,
                "page": d['page'],
                "codice_originale": d['codice_originale'],
                "docling_desc": d['descrizione'],
                "docling_prezzo": float(d['prezzo']) if d['prezzo'] else None,
                "prod_desc": match['descrizione'] if match else None,
                "prod_prezzo": float(match['prezzo']) if match and match['prezzo'] else None
            })
            spot_count += 1
            
        hard_count = 0
        for p in prod_dicts:
            if hard_count >= 4:
                break
            if is_spillover_row(p['descrizione']):
                match = next((d for d in docling_dicts if d['page'] == p['page'] and d['codice_originale'] == p['codice_originale']), None)
                spot_raw.append({
                    "doc": doc_stem,
                    "page": p['page'],
                    "codice_originale": p['codice_originale'],
                    "docling_desc": match['descrizione'] if match else None,
                    "docling_prezzo": float(match['prezzo']) if match and match['prezzo'] else None,
                    "prod_desc": p['descrizione'],
                    "prod_prezzo": float(p['prezzo']) if p['prezzo'] else None,
                    "note": "Prod was soup"
                })
                hard_count += 1

    conn.close()
    
    with open("output/spike_b/docscale/matrix.json", "w") as f:
        json.dump(matrix, f, indent=2)
        
    with open("output/spike_b/docscale/spot.json", "w") as f:
        json.dump(spot_raw, f, indent=2)
        
    print("Done. Saved artifacts.")

if __name__ == "__main__":
    main()
