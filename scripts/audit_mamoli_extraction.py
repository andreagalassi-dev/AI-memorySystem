import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import psycopg
import fitz
from psycopg.rows import dict_row

from src.backend.config import settings
from src.ingestion.extraction.table_detect import is_table_page
from src.ingestion.extraction.text_table import TextTableExtractor
from src.ingestion.extraction.extractor import PageRef

def main():
    doc_stem = "Mamoli_Catalogo_Ricambi_2021_IT_EN"
    pdf_path = Path("corpus/original_dataset") / f"{doc_stem}.pdf"
    
    doc = fitz.open(pdf_path)
    n_pages = len(doc)
    
    expected_pages = []
    for i in range(n_pages):
        page = doc.load_page(i)
        if is_table_page(page).is_table:
            expected_pages.append(i + 1)
            
    db_url = f"postgresql://{settings.postgres_user}:{settings.postgres_password}@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}"
    with psycopg.connect(db_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT page, COUNT(*) as c
                FROM estrazione.righe_listino
                WHERE doc_stem = %s
                GROUP BY page
                ORDER BY page
            """, (doc_stem,))
            rows = cur.fetchall()
            
    db_pages = [r['page'] for r in rows]
    missing_pages = sorted(set(expected_pages) - set(db_pages))
    
    output = {
        "n_pages_pdf": n_pages,
        "db_pages_count": len(db_pages),
        "total_rows_db": sum(r['c'] for r in rows),
        "db_pages": db_pages,
        "expected_pages": expected_pages,
        "missing_pages": missing_pages,
    }
    
    out_dir = Path("output/audit")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "b1c_mamoli_coverage.json"
    with open(out_file, "w") as f:
        json.dump(output, f, indent=2)
        
    print(f"Coverage saved to {out_file}")
    
    # 3. Local extraction
    covered_sample = [154, 155, 185]
    holes_sample = [156, 200, 230]
    
    extractor = TextTableExtractor(doc)
    local_output = {}
    
    for p in covered_sample + holes_sample:
        ref = PageRef(doc_stem, p)
        try:
            righe = extractor.extract(ref)
            page_obj = doc.load_page(p - 1)
            text_preview = page_obj.get_text("text")[:500] # preview text
            local_output[p] = {
                "n_righe": len(righe),
                "righe": [{"codice": r.codice_originale, "desc": r.descrizione, "prezzo": str(r.prezzo)} for r in righe],
                "rejects": extractor.rejects,
                "text_preview": text_preview
            }
            print(f"Page {p}: {len(righe)} righe extracted. Rejects: {len(extractor.rejects)}")
        except Exception as e:
            print(f"Page {p} failed: {e}")
            local_output[p] = {"error": str(e)}
            
    out_local = out_dir / "b1c_mamoli_local_extraction.json"
    with open(out_local, "w") as f:
        json.dump(local_output, f, indent=2)
        
    print(f"Local extraction saved to {out_local}")

if __name__ == '__main__':
    main()
