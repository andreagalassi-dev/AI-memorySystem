import os
import json
import fitz
import psycopg
import sys
import traceback
from decimal import Decimal
from typing import Dict, Any

sys.path.append(".")
from src.ingestion.extraction.docling_extractor import DoclingTableExtractor
from src.ingestion.extraction.extractor import PageRef
from src.ingestion.extraction.load import init_schema, upsert_righe
from src.ingestion.extraction.schema import RigaListino
from src.ingestion.extraction.quality_guard import is_spillover_row
from src.backend.clients.postgres_client import conninfo
from src.ingestion.codes import canonical

DOCS = {
    "Mamoli": "corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf",
    "ivm": "corpus/hardware_fasteners/ivm_edilizia_catalistino_2026.pdf",
    "kohler_marine": "corpus/engines/kohler_marine_tp6954.pdf",
    "nsk": "corpus/price_lists/nsk_cuscinetti_listino_2025.pdf"
}

REAL_STEMS = {k: os.path.basename(v).replace(".pdf", "") for k, v in DOCS.items()}

def run():
    conn = psycopg.connect(conninfo())
    init_schema(conn, schema="b_tmp2")
    
    # Pulizia
    conn.execute("TRUNCATE b_tmp2.righe_listino")
    conn.commit()
    
    stats = {
        "lookahead": [], "suffix": [],
        "short_code_accepted": [], "short_code_rejected": []
    }
    matrix = {}
    
    print("--- STARTING MASSIVE EXTRACTION ---")
    for name, path in DOCS.items():
        doc_stem = REAL_STEMS[name]
        print(f"Extracting {doc_stem}...")
        
        doc = fitz.open(path)
        num_pages = len(doc)
        extractor = DoclingTableExtractor(doc)
        
        matrix[doc_stem] = {
            "total_rows": 0,
            "has_price": 0,
            "is_soup": 0,
            "duplicates": 0,
            "errors": 0,
            "pages_covered": 0
        }
        
        # Estrai range(1, num_pagine+1) INTERO
        for p in range(1, num_pages + 1):
            ref = PageRef(doc_stem=doc_stem, page=p)
            try:
                grezze = extractor.extract(ref, stats=stats)
                if grezze:
                    matrix[doc_stem]["pages_covered"] += 1
                
                righe_listino = []
                for g in grezze:
                    riga = RigaListino.from_grezza(g, doc_stem=doc_stem, page=p)
                    righe_listino.append(riga)
                    
                    matrix[doc_stem]["total_rows"] += 1
                    if riga.prezzo is not None:
                        matrix[doc_stem]["has_price"] += 1
                    if is_spillover_row(riga.descrizione):
                        matrix[doc_stem]["is_soup"] += 1
                        
                if righe_listino:
                    upsert_righe(conn, righe_listino, schema="b_tmp2")
                    conn.commit()
                    
            except Exception as e:
                print(f"Error on {doc_stem} page {p}: {e}")
                traceback.print_exc()
                conn.rollback()
                matrix[doc_stem]["errors"] += 1
                
        # Compare with estrazione
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), COUNT(prezzo) FROM estrazione.righe_listino WHERE doc_stem = %s", (doc_stem,))
        row = cur.fetchone()
        matrix[doc_stem]["estrazione_total_rows"] = row[0]
        matrix[doc_stem]["estrazione_has_price"] = row[1]
        
        # Recall vs produzione
        cur.execute("""
            SELECT codice_normalizzato FROM estrazione.righe_listino 
            WHERE doc_stem = %s 
            EXCEPT 
            SELECT codice_normalizzato FROM b_tmp2.righe_listino 
            WHERE doc_stem = %s
        """, (doc_stem, doc_stem))
        persi = [r[0] for r in cur.fetchall()]
        
        cur.execute("""
            SELECT codice_normalizzato FROM b_tmp2.righe_listino 
            WHERE doc_stem = %s 
            EXCEPT 
            SELECT codice_normalizzato FROM estrazione.righe_listino 
            WHERE doc_stem = %s
        """, (doc_stem, doc_stem))
        recuperati = [r[0] for r in cur.fetchall()]
        
        matrix[doc_stem]["recall_persi"] = len(persi)
        matrix[doc_stem]["recall_recuperati"] = len(recuperati)
        
        if matrix[doc_stem]["total_rows"] > 0:
            matrix[doc_stem]["perc_price"] = matrix[doc_stem]["has_price"] / matrix[doc_stem]["total_rows"] * 100
            matrix[doc_stem]["perc_soup"] = matrix[doc_stem]["is_soup"] / matrix[doc_stem]["total_rows"] * 100
        else:
            matrix[doc_stem]["perc_price"] = 0.0
            matrix[doc_stem]["perc_soup"] = 0.0

    os.makedirs("output/spike_b/db3", exist_ok=True)
    with open("output/spike_b/db3/matrix.json", "w") as f:
        json.dump(matrix, f, indent=2)

    # Heuristics stats (fresh firing counts)
    heuristics_out = {
        "lookahead_count": len(stats["lookahead"]),
        "suffix_count": len(stats["suffix"]),
        "short_code_accepted_count": len(stats["short_code_accepted"]),
        "short_code_rejected_count": len(stats["short_code_rejected"]),
        "lookahead_sample": stats["lookahead"][:5],
        "suffix_sample": stats["suffix"][:5],
        "short_code_accepted_sample": stats["short_code_accepted"][:5],
        "short_code_rejected_sample": stats["short_code_rejected"][:5],
    }
    with open("output/spike_b/db3/heuristics_sample.json", "w") as f:
        json.dump(heuristics_out, f, indent=2)
        
    # New short codes sample: 5 newly emitted short codes for adjudication
    new_shortcodes = []
    for entry in stats["short_code_accepted"][:5]:
        new_shortcodes.append({
            "code": entry[0],
            "doc_stem": entry[1],
            "page": entry[2],
            "row": entry[3]
        })
    with open("output/spike_b/db3/new_shortcodes_sample.json", "w") as f:
        json.dump(new_shortcodes, f, indent=2)
        
    print("\n--- RESULTS ---")
    # (a) SELECT doc_stem,count(distinct page),count(*) FROM b_tmp2.righe_listino GROUP BY doc_stem
    print("\n(a) b_tmp2 Coverage:")
    cur = conn.cursor()
    cur.execute("SELECT doc_stem, count(distinct page), count(*) FROM b_tmp2.righe_listino GROUP BY doc_stem")
    for r in cur.fetchall():
        print(f"  {r[0]}: pages={r[1]}, rows={r[2]}")

    # (b) golden score
    truth = json.load(open("output/spike_b/goldenset/reviewer_truth.json"))
    correct = 0
    total = 0
    results_detail = []
    for item in truth["items"]:
        if "_section" in item:
            continue
        total += 1
        stem = REAL_STEMS[item["doc"]]
        page = item["page"]
        code = item["code"]
        true_prezzo = Decimal(str(item["true_prezzo"])) if item.get("true_prezzo") is not None else None
        must_be_absent = item.get("must_be_absent", False)
        
        cur.execute(
            "SELECT prezzo FROM b_tmp2.righe_listino WHERE doc_stem = %s AND page = %s AND codice_normalizzato = %s",
            (stem, page, canonical(code))
        )
        row = cur.fetchone()
        
        db_prezzo = None
        found = row is not None
        if found and row[0] is not None:
             db_prezzo = Decimal(str(row[0]))
        
        ok = False
        if must_be_absent:
            ok = not found
            results_detail.append({"code": code, "page": page, "doc": item["doc"],
                                   "expected": "ABSENT", "found": found, "ok": ok})
        elif true_prezzo is None and db_prezzo is None and found:
             ok = True
             results_detail.append({"code": code, "page": page, "doc": item["doc"],
                                    "expected_prezzo": None, "db_prezzo": None, "ok": ok})
        elif true_prezzo is not None and db_prezzo == true_prezzo:
             ok = True
             results_detail.append({"code": code, "page": page, "doc": item["doc"],
                                    "expected_prezzo": str(true_prezzo), "db_prezzo": str(db_prezzo), "ok": ok})
        else:
             results_detail.append({"code": code, "page": page, "doc": item["doc"],
                                    "expected_prezzo": str(true_prezzo) if true_prezzo else None,
                                    "db_prezzo": str(db_prezzo) if db_prezzo else None,
                                    "found": found, "ok": ok})
        if ok:
            correct += 1
             
    print(f"\n(b) Golden set score (b_tmp2): {correct}/{total}")
    for d in results_detail:
        status = "✅" if d["ok"] else "❌"
        print(f"  {status} {d['doc']} p{d['page']} {d['code']}: {d}")
    
    with open("output/spike_b/db3/golden_results.json", "w") as f:
        json.dump({"score": f"{correct}/{total}", "details": results_detail}, f, indent=2, default=str)

    # (c) recall per-doc
    print("\n(c) Recall per doc (recuperati vs persi):")
    for doc_stem, m in matrix.items():
        print(f"  {doc_stem}: recuperati={m['recall_recuperati']}, persi={m['recall_persi']}")
        
    # (d) firing counts (FRESH)
    print(f"\n(d) Firing counts:")
    print(f"  lookahead={len(stats['lookahead'])}")
    print(f"  suffix={len(stats['suffix'])}")
    print(f"  short_code_accepted={len(stats['short_code_accepted'])}")
    print(f"  short_code_rejected={len(stats['short_code_rejected'])}")
    print("  Samples saved in output/spike_b/db3/heuristics_sample.json")
    print("  New short codes sample saved in output/spike_b/db3/new_shortcodes_sample.json")
    
    # (e) matrix
    print("\n(e) Matrix saved in output/spike_b/db3/matrix.json")
    
    # (f) conteggi DB invariati
    cur.execute("SELECT count(*) FROM estrazione.righe_listino")
    estrazione_count = cur.fetchone()[0]
    cur.execute("SELECT count(distinct doc_stem) FROM estrazione.righe_listino")
    estrazione_docs = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM b_tmp.righe_listino")
    btmp_count = cur.fetchone()[0]
    print(f"\n(f) DB Invariants:")
    print(f"  estrazione.righe_listino: {estrazione_count} rows, {estrazione_docs} docs")
    print(f"  b_tmp.righe_listino: {btmp_count} rows")

    conn.close()

if __name__ == "__main__":
    run()
