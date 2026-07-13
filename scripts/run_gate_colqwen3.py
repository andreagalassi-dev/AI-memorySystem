import json
import sys
import os
from pathlib import Path
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.retrieval.colqwen3 import embed_pages, VISUAL_COLLECTION_COLQWEN3
from src.backend.config import settings
from qdrant_client import QdrantClient
import uuid

def page_id(doc: str, page: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"visual_colqwen3/{doc}/p{page}"))

def main():
    samples_file = sys.argv[1]
    with open(samples_file) as f:
        samples = json.load(f)
        
    gate_client = QdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        api_key=settings.qdrant_api_key or None,
        timeout=300.0,
    )
    
    success = True
    tested = 0
    failures = []
    audit_data = {"samples": []}
    
    for i, m in enumerate(samples):
        pid = page_id(m["doc"], m["page"])
        png_path = ROOT / m["render"]
        img = Image.open(png_path).convert("RGB")
        print(f"Gate {i+1}/{len(samples)}: {pid} ({png_path.name})", flush=True)
        embs = embed_pages([img])
        emb = embs[0]
        n_vettori_query = len(emb)
        
        hits = gate_client.query_points(
            VISUAL_COLLECTION_COLQWEN3, 
            query=emb,
            limit=2,
            with_payload=False
        ).points
        
        sample_audit = {
            "pid": pid,
            "n_vettori": n_vettori_query,
            "top1_id": hits[0].id if hits else None,
            "top1_score": hits[0].score if hits else None,
            "score_per_token": (hits[0].score / n_vettori_query) if hits and n_vettori_query else None
        }
        audit_data["samples"].append(sample_audit)
        
        if not hits or hits[0].id != pid:
            print(f"Validation Gate failed for {pid}: non è top-1", file=sys.stderr)
            success = False
            failures.append(pid)
        elif sample_audit["score_per_token"] < 0.85:
            print(f"Validation Gate failed for {pid}: score_per_token {sample_audit['score_per_token']:.3f} < 0.85", file=sys.stderr)
            success = False
            failures.append(pid)
            
        tested += 1
                
    audit_data["passed"] = success
    audit_data["samples_tested"] = tested
    audit_data["failures"] = failures
    
    out_path = ROOT / "output" / "eval" / "colqwen3_ingest_gate.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w") as f:
        json.dump(audit_data, f, indent=2)
        
    return 0 if success else 1

if __name__ == "__main__":
    sys.exit(main())
