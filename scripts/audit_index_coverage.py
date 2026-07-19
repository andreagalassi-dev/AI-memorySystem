#!/usr/bin/env python3
import sys
from pathlib import Path
import fnmatch
import json
from collections import defaultdict
import psycopg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

def classify_pdf(pdf_path: Path, doc_slugs: set[str], doc_map_patterns: list[str]) -> str:
    if pdf_path.suffix.lower() != '.pdf':
        return "FORMATO_NON_GESTITO"
        
    stem = pdf_path.stem
    name = pdf_path.name
    matches = {s for s in doc_slugs if s == stem or s == name}
    
    if len(matches) > 1:
        return "DA_INDAGARE"
    if len(matches) == 1:
        return "INDICIZZATO"
        
    matched_patterns = [pat for pat in doc_map_patterns if fnmatch.fnmatch(name, pat)]
    if not matched_patterns:
        return "MAI_TENTATO"
        
    try:
        from src.ingestion.fast_dispatch import dispatch_pdf, CorruptPdfError, EncryptedPdfError
        dispatch_pdf(str(pdf_path), extract_text=False)
    except EncryptedPdfError:
        return "SCARTATO_DISPATCH"
    except CorruptPdfError:
        return "FORMATO_NON_GESTITO"
    except Exception:
        return "SCARTATO_DISPATCH"
        
    return "FALLITO_ESTRAZIONE"

def main():
    from src.backend.config import settings as s

    conn = psycopg.connect(
        dbname=s.postgres_db,
        user=s.postgres_user,
        password=s.postgres_password,
        host=s.postgres_host,
        port=s.postgres_port,
    )
    with conn.cursor() as cur:
        cur.execute("SELECT slug FROM public.documents")
        doc_slugs = {row[0] for row in cur.fetchall()}
        
        cur.execute("SELECT glob_pattern FROM public.ingestion_doc_map")
        doc_map_patterns = [row[0] for row in cur.fetchall()]
        
    pdfs = list((ROOT / "corpus").rglob("*.pdf"))
    
    reasons_count = defaultdict(int)
    category_count = defaultdict(lambda: defaultdict(int))
    da_indagare_files = []
    
    # Per output json
    audit_data = {
        "totale_pdf_trovati": len(pdfs),
        "indicizzati": 0,
        "assenti": 0,
        "motivi": defaultdict(int),
        "dettaglio_file": []
    }
    
    for p in pdfs:
        cat = p.parent.name
        if cat == "corpus":
            cat = "root"
            
        reason = classify_pdf(p, doc_slugs, doc_map_patterns)
        
        if reason == "INDICIZZATO":
            audit_data["indicizzati"] += 1
            continue
            
        audit_data["assenti"] += 1
        reasons_count[reason] += 1
        audit_data["motivi"][reason] += 1
        category_count[cat][reason] += 1
        
        audit_data["dettaglio_file"].append({
            "filepath": str(p.relative_to(ROOT)),
            "category": cat,
            "motivo": reason
        })
        
        if reason == "DA_INDAGARE":
            da_indagare_files.append(str(p.relative_to(ROOT)))
            
    # Stampa risultati
    print("="*60)
    print("CENSIMENTO INDICE COVERAGE (B1.a)")
    print("="*60)
    print(f"Totale PDF in corpus/: {audit_data['totale_pdf_trovati']}")
    print(f"Indicizzati univocamente: {audit_data['indicizzati']}")
    print(f"Assenti: {audit_data['assenti']}")
    print("-" * 60)
    print(f"{'MOTIVO':<25} | {'CONTEGGIO':<10}")
    print("-" * 60)
    for reason, count in sorted(reasons_count.items()):
        print(f"{reason:<25} | {count:<10}")
        
    print("\n" + "="*60)
    print("CATEGORIE DEI FILE ASSENTI")
    print("="*60)
    for cat, reasons in sorted(category_count.items()):
        print(f"[{cat}]")
        for reason, count in sorted(reasons.items()):
            print(f"  - {reason}: {count}")
            
    if da_indagare_files:
        print("\n" + "="*60)
        print("ELENCO 'DA_INDAGARE' (match ambiguo o multiplo)")
        print("="*60)
        for f in da_indagare_files:
            print(f" - {f}")
            
    out_dir = ROOT / "output" / "audit"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "b1a_coverage.json"
    
    with open(out_file, "w") as f:
        json.dump(audit_data, f, indent=2)
    print(f"\nDettagli salvati in: {out_file.relative_to(ROOT)}")
    
if __name__ == "__main__":
    main()
