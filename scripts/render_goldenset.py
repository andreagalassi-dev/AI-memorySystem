import os
import json
import random
from pathlib import Path
import fitz
import psycopg
from src.backend.clients.postgres_client import conninfo

DOCS = [
    'Mamoli_Catalogo_Ricambi_2021_IT_EN',
    'ivm_edilizia_catalistino_2026',
    'kohler_marine_tp6954',
    'nsk_cuscinetti_listino_2025'
]

def main():
    conn = psycopg.connect(conninfo())
    cur = conn.cursor()
    
    # Load data from estrazione
    cur.execute("SELECT doc_stem, page, codice_originale, codice_normalizzato, prezzo, descrizione FROM estrazione.righe_listino")
    prod_rows = cur.fetchall()
    
    # Load data from b_tmp
    cur.execute("SELECT doc_stem, page, codice_originale, codice_normalizzato, prezzo, descrizione FROM b_tmp.righe_listino")
    btmp_rows = cur.fetchall()
    
    items = {}
    for r in prod_rows:
        doc, page, code_orig, code_norm, price, desc = r
        if doc not in DOCS: continue
        key = (doc, page, code_orig)
        items[key] = {
            'doc': doc, 'page': page, 'code': code_orig, 'code_norm': code_norm,
            'prod_prezzo': float(price) if price is not None else None, 
            'prod_desc': desc,
            'btmp_prezzo': None, 'btmp_desc': None
        }
        
    for r in btmp_rows:
        doc, page, code_orig, code_norm, price, desc = r
        if doc not in DOCS: continue
        key = (doc, page, code_orig)
        if key not in items:
            items[key] = {
                'doc': doc, 'page': page, 'code': code_orig, 'code_norm': code_norm,
                'prod_prezzo': None, 'prod_desc': None,
                'btmp_prezzo': float(price) if price is not None else None, 
                'btmp_desc': desc
            }
        else:
            items[key]['btmp_prezzo'] = float(price) if price is not None else None
            items[key]['btmp_desc'] = desc

    # Fetch languages
    cur.execute("SELECT doc_stem, langs FROM public.document_versions")
    langs_map = {r[0]: ",".join(r[1]) if r[1] else "und" for r in cur.fetchall()}
    
    filtered_items = []
    for k, v in items.items():
        if v['doc'] == 'nsk_cuscinetti_listino_2025':
            # Evita pagine di sole equivalenze con "*" e nessun prezzo: filter out items with no price for safety
            if v['prod_prezzo'] is None and v['btmp_prezzo'] is None:
                continue
        filtered_items.append(v)
        
    # Group by doc
    by_doc = {}
    for item in filtered_items:
        item['lang'] = langs_map.get(item['doc'], 'und')
        by_doc.setdefault(item['doc'], []).append(item)
        
    random.seed(7)
    
    os.makedirs("output/spike_b/goldenset/crops", exist_ok=True)
    
    doc_pdfs = {}
    for doc in DOCS:
        paths = list(Path("corpus").rglob(f"{doc}.pdf"))
        if paths:
            doc_pdfs[doc] = fitz.open(paths[0])
            
    goldenset = []
    
    for doc, arr in by_doc.items():
        arr.sort(key=lambda x: (x['page'], x['code'])) # Deterministic order before shuffle
        
        with_price = [x for x in arr if x['prod_prezzo'] is not None or x['btmp_prezzo'] is not None]
        without_price = [x for x in arr if x['prod_prezzo'] is None and x['btmp_prezzo'] is None]
        
        random.shuffle(with_price)
        random.shuffle(without_price)
        
        pool = with_price + without_price
        
        pdf = doc_pdfs.get(doc)
        if not pdf: 
            print(f"PDF not found for {doc}")
            continue
            
        success_count = 0
        for item in pool:
            if success_count >= 13: break
            
            page_idx = item['page'] - 1
            if page_idx < 0 or page_idx >= len(pdf): continue
            page_obj = pdf[page_idx]
            
            code = item['code']
            code_norm = item['code_norm'] or ""
            
            bbox = None
            for cand in [code, code_norm]:
                if not cand: continue
                rects = page_obj.search_for(cand)
                if rects:
                    bbox = rects[0]
                    break
                    
            if not bbox:
                for w in page_obj.get_text("words"):
                    if code in w[4] or (code_norm and code_norm in w[4]):
                        bbox = fitz.Rect(w[:4])
                        break
                        
            if bbox:
                annot = page_obj.add_rect_annot(bbox)
                annot.set_colors(stroke=(1, 0, 0))
                annot.update()
                
                context = 30
                crop_rect = fitz.Rect(0, max(0, bbox.y0 - context), page_obj.rect.width, min(page_obj.rect.height, bbox.y1 + context))
                pix = page_obj.get_pixmap(matrix=fitz.Matrix(200/72, 200/72), clip=crop_rect)
                
                safe_code = code.replace("/", "_").replace("\\", "_")
                fname = f"{doc}_p{item['page']}_{safe_code}.png"
                crop_path = f"output/spike_b/goldenset/crops/{fname}"
                pix.save(crop_path)
                
                page_obj.delete_annot(annot)
                
                table_item = {
                    "doc": doc,
                    "page": item['page'],
                    "code": code,
                    "lang": item['lang'],
                    "btmp_prezzo": item['btmp_prezzo'],
                    "btmp_desc": item['btmp_desc'],
                    "prod_prezzo": item['prod_prezzo'],
                    "prod_desc": item['prod_desc'],
                    "crop_path": crop_path
                }
                goldenset.append(table_item)
                success_count += 1
                
    with open("output/spike_b/goldenset/table.json", "w", encoding="utf-8") as f:
        json.dump(goldenset, f, indent=2, ensure_ascii=False)
        
if __name__ == "__main__":
    main()
