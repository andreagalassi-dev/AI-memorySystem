import sys
import os
import json
import psycopg
from collections import defaultdict
from pathlib import Path
from qdrant_client import QdrantClient
from qdrant_client.http import models

sys.path.append(str(Path(__file__).parent.parent))
from src.backend.config import settings

class GoldenValidator:
    def __init__(self, qdrant_client=None, pg_conn=None):
        self.qdrant = qdrant_client
        self.pg = pg_conn
        
    def validate_row(self, row):
        intent = row["intent"]
        expected = row.get("expected", {})
        
        if intent == "refusal":
            return True, ""
            
        doc = expected.get("doc")
        
        if intent == "code":
            page = expected.get("page")
            code = expected.get("code")
            
            # 1. Check Qdrant aike_chunks
            results = self.qdrant.scroll(
                collection_name='aike_chunks',
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="doc", match=models.MatchValue(value=doc)),
                        models.FieldCondition(key="page", match=models.MatchValue(value=page)),
                        models.FieldCondition(key="code", match=models.MatchValue(value=code))
                    ]
                ),
                limit=1
            )[0]
            if results:
                return True, ""
                
            # 2. Check Postgres estrazione.righe_listino
            with self.pg.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM estrazione.righe_listino WHERE doc_stem = %s AND page = %s AND (codice_originale ILIKE %s OR codice_normalizzato ILIKE %s)",
                    (doc, page, code, code)
                )
                if cur.fetchone():
                    return True, ""
                    
            return False, f"Code {code} not found in doc {doc} page {page}"
            
        elif intent == "visual":
            pages = expected.get("pages", [])
            for p in pages:
                results = self.qdrant.scroll(
                    collection_name='aike_pages_visual',
                    scroll_filter=models.Filter(
                        must=[
                            models.FieldCondition(key="doc", match=models.MatchValue(value=doc)),
                            models.FieldCondition(key="page", match=models.MatchValue(value=p))
                        ]
                    ),
                    limit=1
                )[0]
                if not results:
                    return False, f"Page {p} not found in visual index for doc {doc}"
            return True, ""
            
        elif intent == "narrative":
            page = expected.get("page")
            results = self.qdrant.scroll(
                collection_name='aike_chunks',
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(key="doc", match=models.MatchValue(value=doc)),
                        models.FieldCondition(key="page", match=models.MatchValue(value=page))
                    ]
                ),
                limit=1
            )[0]
            if not results:
                return False, f"Page {page} not found in chunks for doc {doc}"
            return True, ""
            
        elif intent == "rbac_negative":
            role = row.get("role")
            with self.pg.cursor() as cur:
                cur.execute(
                    """
                    SELECT 1 
                    FROM document_roles dr
                    JOIN documents d ON dr.document_id = d.id
                    WHERE d.slug = %s AND dr.role = %s
                    """,
                    (doc, role)
                )
                if cur.fetchone():
                    return False, f"RBAC finta: doc {doc} è accessibile al ruolo {role}"
            return True, ""
            
        return False, "Unknown intent"

def main():
    qdrant = QdrantClient(url=f"http://{settings.qdrant_host}:{settings.qdrant_port}")
    conn = psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        dbname=settings.postgres_db
    )
    
    validator = GoldenValidator(qdrant, conn)
    
    results_by_group = defaultdict(lambda: {"total": 0, "answerable": 0})
    failures = []
    audit_data = []
    
    with open("data/golden/golden_v1.jsonl", "r") as f:
        for line in f:
            if not line.strip(): continue
            row = json.loads(line)
            
            ok, err = validator.validate_row(row)
            
            group_key = f"{row['intent']}/{row['lang']}"
            results_by_group[group_key]["total"] += 1
            if ok:
                results_by_group[group_key]["answerable"] += 1
            else:
                failures.append((row['qa_id'], err))
                
            audit_data.append({
                "qa_id": row["qa_id"],
                "answerable": ok,
                "error": err
            })
            
    with open("output/audit/b2_1_validation.json", "w") as f:
        json.dump(audit_data, f, indent=2)
        
    print(f"{'Intent/Lang':<20} | {'Total':<10} | {'Answerable':<10}")
    print("-" * 45)
    for k, v in sorted(results_by_group.items()):
        print(f"{k:<20} | {v['total']:<10} | {v['answerable']:<10}")
        
    if failures:
        print("\nFailures:")
        for qa_id, err in failures:
            print(f"- {qa_id}: {err}")
        sys.exit(1)
        
    sys.exit(0)

if __name__ == "__main__":
    main()
