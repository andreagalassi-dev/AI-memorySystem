import os
import sys
import re
import random
import json
import psycopg
import fitz

random.seed(42)

DEFAULT_CODE_RE = re.compile(r"^(?=.*[A-Z])(?=(?:.*\d){4,})[A-Z0-9]{6,}$")

DOCS = [
    "nsk_cuscinetti_listino_2025",
    "kohler_marine_tp6954",
    "ivm_edilizia_catalistino_2026",
    "Mamoli_Catalogo_Ricambi_2021_IT_EN"
]

PDF_PATHS = {
    "nsk_cuscinetti_listino_2025": "./corpus/price_lists/nsk_cuscinetti_listino_2025.pdf",
    "kohler_marine_tp6954": "./corpus/engines/kohler_marine_tp6954.pdf",
    "ivm_edilizia_catalistino_2026": "./corpus/hardware_fasteners/ivm_edilizia_catalistino_2026.pdf",
    "Mamoli_Catalogo_Ricambi_2021_IT_EN": "./corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf"
}

def get_connection():
    return psycopg.connect("postgresql://aike:aike_dev@localhost:5432/aike_registry")

def group_into_ybands(words):
    if not words:
        return []
    words = sorted(words, key=lambda w: (w[1], w[0]))
    bands = []
    current_band = [words[0]]
    curr_y0, curr_y1 = words[0][1], words[0][3]

    for w in words[1:]:
        x0, y0, x1, y1 = w[:4]
        overlap = max(0, min(curr_y1, y1) - max(curr_y0, y0))
        h1 = curr_y1 - curr_y0
        h2 = y1 - y0
        if h1 > 0 and h2 > 0 and overlap > 0.4 * min(h1, h2):
            current_band.append(w)
            curr_y0 = min(curr_y0, y0)
            curr_y1 = max(curr_y1, y1)
        else:
            bands.append(current_band)
            current_band = [w]
            curr_y0, curr_y1 = y0, y1
            
    if current_band:
        bands.append(current_band)
        
    for band in bands:
        band.sort(key=lambda w: w[0])
    return bands

def get_all_codes_in_band(band):
    text_no_spaces = "".join(re.sub(r'\s+', '', w[4]) for w in band)
    candidates = re.findall(r"[A-Z0-9]{6,}", text_no_spaces)
    valid_codes = set()
    for c in candidates:
        if DEFAULT_CODE_RE.match(c):
            valid_codes.add(c)
    return valid_codes

def find_code_in_band(band, code_norm):
    text_no_spaces = ""
    word_map = []
    for i, w in enumerate(band):
        c = re.sub(r'\s+', '', w[4])
        word_map.extend([i] * len(c))
        text_no_spaces += c
        
    idx = text_no_spaces.find(code_norm)
    if idx != -1:
        end_idx = idx + len(code_norm) - 1
        return word_map[end_idx]
    return -1

def extract_gt(band, code_idx):
    tail = band[code_idx+1:]
    if not tail:
        return "", None
        
    price_idx = -1
    price_val = None
    
    for i in range(len(tail)-1, -1, -1):
        text = tail[i][4]
        text_clean = re.sub(r'[^\d,\.]', '', text)
        if text_clean and re.search(r'\d', text_clean):
            try:
                if ',' in text_clean and '.' in text_clean:
                    if text_clean.rfind(',') > text_clean.rfind('.'):
                        val = text_clean.replace('.', '').replace(',', '.')
                    else:
                        val = text_clean.replace(',', '')
                elif ',' in text_clean:
                    parts = text_clean.rsplit(',', 1)
                    if len(parts) > 1 and len(parts[1]) == 3:
                        val = text_clean.replace(',', '')
                    else:
                        val = text_clean.replace(',', '.')
                else:
                    val = text_clean
                
                price_val = float(val)
                price_idx = i
                break
            except ValueError:
                pass
                
    if price_idx != -1:
        desc_words = tail[:price_idx]
    else:
        desc_words = tail
        
    desc = " ".join(w[4] for w in desc_words).strip()
    return desc, price_val

def compare_desc(pred, gt):
    if not pred or not gt:
        return False
    pred_tokens = set(re.findall(r'\w+', pred.lower()))
    gt_tokens = set(re.findall(r'\w+', gt.lower()))
    if not gt_tokens:
        return False
    overlap = len(pred_tokens & gt_tokens) / len(gt_tokens)
    return overlap >= 0.5

def main():
    os.makedirs("output/spike_b/groundtruth/crops", exist_ok=True)
    
    conn = get_connection()
    cur = conn.cursor()
    
    report = {
        "prezzo_accuracy": {"docling": 0, "prod": 0, "total_eval": 0},
        "desc_corretta_pct": {"docling": 0, "prod": 0, "total_eval": 0},
        "codice_hallucinato_pct": {"docling": 0, "prod": 0, "total": 0},
        "recall_pct": {"docling_found": 0, "prod_found": 0, "total_gt": 0}
    }
    
    items_out = []
    crops_done = 0
    
    for doc in DOCS:
        cur.execute("""
            SELECT page, codice_normalizzato, 
                   MAX(b_tmp_prezzo) as docling_prezzo, 
                   MAX(estr_prezzo) as prod_prezzo,
                   MAX(b_tmp_desc) as docling_desc,
                   MAX(estr_desc) as prod_desc
            FROM (
                SELECT page, codice_normalizzato, prezzo as b_tmp_prezzo, descrizione as b_tmp_desc, NULL::numeric as estr_prezzo, NULL as estr_desc
                FROM b_tmp.righe_listino WHERE doc_stem = %s
                UNION ALL
                SELECT page, codice_normalizzato, NULL::numeric, NULL, prezzo as estr_prezzo, descrizione as estr_desc
                FROM estrazione.righe_listino WHERE doc_stem = %s
            ) as t
            GROUP BY page, codice_normalizzato
        """, (doc, doc))
        
        results = cur.fetchall()
        
        pages_dict = {}
        for r in results:
            page, code, d_p, p_p, d_d, p_d = r
            pages_dict.setdefault(page, []).append({
                "page": page, "code": code, 
                "docling_prezzo": float(d_p) if d_p is not None else None, 
                "prod_prezzo": float(p_p) if p_p is not None else None,
                "docling_desc": d_d, "prod_desc": p_d
            })
            
        sample = []
        page_keys = list(pages_dict.keys())
        random.shuffle(page_keys)
        
        idx = 0
        while len(sample) < 30 and pages_dict:
            p = page_keys[idx % len(page_keys)]
            if pages_dict[p]:
                item = random.choice(pages_dict[p])
                pages_dict[p].remove(item)
                sample.append(item)
            else:
                del pages_dict[p]
                page_keys.remove(p)
                if not page_keys:
                    break
            idx += 1
            
        pdf_path = PDF_PATHS[doc]
        pdf_doc = fitz.open(pdf_path)
        
        sampled_pages = set(item['page'] for item in sample)
        
        # Calculate recall per page
        for p in sampled_pages:
            try:
                page_obj = pdf_doc[p - 1]
            except IndexError:
                continue
            words = page_obj.get_text("words")
            bands = group_into_ybands(words)
            gt_codes = set()
            for band in bands:
                gt_codes.update(get_all_codes_in_band(band))
                
            cur.execute("SELECT codice_normalizzato FROM b_tmp.righe_listino WHERE doc_stem = %s AND page = %s", (doc, p))
            docling_codes = set(r[0] for r in cur.fetchall())
            
            cur.execute("SELECT codice_normalizzato FROM estrazione.righe_listino WHERE doc_stem = %s AND page = %s", (doc, p))
            prod_codes = set(r[0] for r in cur.fetchall())
            
            report["recall_pct"]["total_gt"] += len(gt_codes)
            report["recall_pct"]["docling_found"] += len(gt_codes & docling_codes)
            report["recall_pct"]["prod_found"] += len(gt_codes & prod_codes)
            
        doc_crops = 0
        
        for item in sample:
            p = item['page']
            code = item['code']
            try:
                page_obj = pdf_doc[p - 1]
            except IndexError:
                continue
            words = page_obj.get_text("words")
            bands = group_into_ybands(words)
            
            gt_desc = None
            gt_prezzo = None
            raw_words = []
            found_band = None
            
            for band in bands:
                idx = find_code_in_band(band, code)
                if idx != -1:
                    gt_desc, gt_prezzo = extract_gt(band, idx)
                    raw_words = band
                    found_band = band
                    break
                    
            item["gt_desc"] = gt_desc
            item["gt_prezzo"] = gt_prezzo
            item["raw_words"] = [{"x0": w[0], "y0": w[1], "x1": w[2], "y1": w[3], "text": w[4]} for w in raw_words]
            item["doc"] = doc
            
            # Verdetto
            if not found_band:
                report["codice_hallucinato_pct"]["total"] += 1
                if item["docling_prezzo"] is not None or item["docling_desc"] is not None:
                    report["codice_hallucinato_pct"]["docling"] += 1
                if item["prod_prezzo"] is not None or item["prod_desc"] is not None:
                    report["codice_hallucinato_pct"]["prod"] += 1
                item["verdetto_docling"] = "assente vs gt"
                item["verdetto_prod"] = "assente vs gt"
            else:
                report["prezzo_accuracy"]["total_eval"] += 1
                report["desc_corretta_pct"]["total_eval"] += 1
                
                if item["docling_prezzo"] is not None and gt_prezzo is not None and abs(item["docling_prezzo"] - gt_prezzo) < 0.01:
                    report["prezzo_accuracy"]["docling"] += 1
                    item["verdetto_docling"] = "corretto"
                else:
                    item["verdetto_docling"] = "sbagliato"
                    
                if item["prod_prezzo"] is not None and gt_prezzo is not None and abs(item["prod_prezzo"] - gt_prezzo) < 0.01:
                    report["prezzo_accuracy"]["prod"] += 1
                    item["verdetto_prod"] = "corretto"
                else:
                    item["verdetto_prod"] = "sbagliato"
                    
                if compare_desc(item["docling_desc"], gt_desc):
                    report["desc_corretta_pct"]["docling"] += 1
                if compare_desc(item["prod_desc"], gt_desc):
                    report["desc_corretta_pct"]["prod"] += 1
                    
                # Crop
                if doc_crops < 5:
                    rect = fitz.Rect(min(w[0] for w in found_band), min(w[1] for w in found_band), 
                                     max(w[2] for w in found_band), max(w[3] for w in found_band))
                    clip = rect + (-10, -10, 10, 10)
                    pix = page_obj.get_pixmap(dpi=150, clip=clip)
                    safe_code = code.replace('/', '_')
                    pix.save(f"output/spike_b/groundtruth/crops/{doc}_p{p}_{safe_code}.png")
                    doc_crops += 1
                    crops_done += 1
            
            items_out.append(item)
            
    with open("output/spike_b/groundtruth/items.json", "w", encoding="utf-8") as f:
        json.dump(items_out, f, indent=2, ensure_ascii=False)
        
    # Convert counts to percentages
    def pct(num, den):
        return round((num / den * 100) if den > 0 else 0, 1)
        
    final_report = {
        "prezzo_accuracy": {
            "docling_pct": pct(report["prezzo_accuracy"]["docling"], report["prezzo_accuracy"]["total_eval"]),
            "prod_pct": pct(report["prezzo_accuracy"]["prod"], report["prezzo_accuracy"]["total_eval"]),
            "total_eval": report["prezzo_accuracy"]["total_eval"]
        },
        "desc_corretta_pct": {
            "docling_pct": pct(report["desc_corretta_pct"]["docling"], report["desc_corretta_pct"]["total_eval"]),
            "prod_pct": pct(report["desc_corretta_pct"]["prod"], report["desc_corretta_pct"]["total_eval"]),
            "total_eval": report["desc_corretta_pct"]["total_eval"]
        },
        "codice_hallucinato_pct": {
            "docling_pct": pct(report["codice_hallucinato_pct"]["docling"], report["codice_hallucinato_pct"]["total"]),
            "prod_pct": pct(report["codice_hallucinato_pct"]["prod"], report["codice_hallucinato_pct"]["total"]),
            "total_hallucinated_candidates": report["codice_hallucinato_pct"]["total"]
        },
        "recall_pct": {
            "docling_pct": pct(report["recall_pct"]["docling_found"], report["recall_pct"]["total_gt"]),
            "prod_pct": pct(report["recall_pct"]["prod_found"], report["recall_pct"]["total_gt"]),
            "total_gt_codes": report["recall_pct"]["total_gt"]
        }
    }
    
    with open("output/spike_b/groundtruth/report.json", "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2)

if __name__ == "__main__":
    main()
