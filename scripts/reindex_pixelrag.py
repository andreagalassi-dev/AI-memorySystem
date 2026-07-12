"""Punto 0.2 — Re-ingestione visiva PixelRAG (Single Vector MRL 1024).

Legge _route_colpali.txt, applica il tiling di PixelRAG (tile+thumb), embedda
tramite VisualEmbedder (Qwen3-VL single vector), e carica su Qdrant (VisualIndex).
Successivamente backfill_qdrant.py aggiungerà i ruoli.

Uso: uv run scripts/reindex_pixelrag.py
"""
import json
import os
import resource
import sys
import time
from pathlib import Path
import torch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz

from src.backend.clients.qdrant_client import get_qdrant_client
from src.retrieval.qwen3vl import ensure_visual_collection
from src.retrieval.pixelrag.index import QdrantVisualIndex, VisualPoint, KIND_TILE, KIND_THUMB
from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder
from src.retrieval.pixelrag.tiling import render_page_tiles
from src.serving.multiplexer import ModelMultiplexer
from src.retrieval.pixelrag.ingest import _point_id, decide_recreate, get_successful_pages

ROUTE = Path(os.environ.get("VISUAL_ROUTE", ROOT / "output" / "spike" / "page_classes" / "_route_colpali.txt"))
RENDERS = ROOT / "output" / "visual" / "renders"
PROGRESS = ROOT / "output" / "visual" / "_progress.jsonl"
CORPUS = ROOT / "corpus"
BATCH = 2
LIMIT = int(os.environ.get("VISUAL_LIMIT", "0"))

def render_page(pdf_path: Path, pno: int, out_png: Path):
    doc = fitz.open(pdf_path)
    page = doc[pno - 1]
    
    # Create rendering dirs
    out_png.parent.mkdir(parents=True, exist_ok=True)
    
    crops, thumb = render_page_tiles(page)
    # Save the thumb as the official render for the UI
    thumb.save(out_png)
    
    doc.close()
    return crops, thumb

def main() -> int:
    refs = []
    for line in ROUTE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ref, p = line.rsplit("#p", 1)
            refs.append((ref, int(p)))
            
    client = get_qdrant_client()
    progress_exists = PROGRESS.exists()
    env_recreate = os.environ.get("VISUAL_RECREATE")
    
    recreate = decide_recreate(progress_exists, env_recreate)
    ensure_visual_collection(client, recreate=recreate)
    
    index = QdrantVisualIndex(client)
    from src.serving.multiplexer import ModelMultiplexer, FakeModelRuntime
    mx = ModelMultiplexer(runtime=FakeModelRuntime())
    embedder = Qwen3VLEmbedder(multiplexer=mx)
    
    done_ids = set()
    if progress_exists and env_recreate != "1":
        for line in PROGRESS.open(encoding="utf-8"):
            e = json.loads(line)
            if e.get("ok"):
                done_ids.add((e["doc"], e["page"]))
    else:
        PROGRESS.parent.mkdir(parents=True, exist_ok=True)
        if progress_exists: PROGRESS.unlink()

    progress = PROGRESS.open("a", encoding="utf-8")
    todo = [(r, p) for r, p in refs if (Path(r).stem, p) not in done_ids]
    print(f"[visual] pagine totali={len(refs)} da fare={len(todo)}")

    t_start = time.time()
    done = failed = 0
    for start in range(0, len(todo), BATCH):
        if LIMIT and done >= LIMIT:
            break
        batch = todo[start:start + BATCH]
        
        all_images = []
        all_points_meta = []
        failed_renders = set()
        
        for ref, pno in batch:
            stem = Path(ref).stem
            png = RENDERS / stem / f"p{pno:04d}.png"
            try:
                crops, thumb = render_page(CORPUS / ref, pno, png)
                # Append all images for this page
                for tile, img in crops:
                    all_images.append(img)
                    all_points_meta.append({
                        "doc": stem, "page": pno, "kind": KIND_TILE,
                        "row": tile.row, "col": tile.col, "cat": ref.split("/")[0],
                        "render": str(png.relative_to(ROOT)), "bbox": [tile.x0, tile.y0, tile.w, tile.h]
                    })
                all_images.append(thumb)
                all_points_meta.append({
                    "doc": stem, "page": pno, "kind": KIND_THUMB,
                    "row": -1, "col": -1, "cat": ref.split("/")[0],
                    "render": str(png.relative_to(ROOT)), "bbox": None
                })
            except Exception as exc:
                progress.write(json.dumps({"doc": stem, "page": pno, "ok": False, "error": str(exc)}) + "\n")
                progress.flush()
                failed_renders.add((ref, pno))
                failed += 1
                
        if not all_images:
            continue
            
        t0 = time.time()
        try:
            vecs = embedder.embed_tiles(all_images)
        except Exception as exc:
            print(f"Failed embed: {exc}")
            failed += len(batch)
            continue
            
        points = []
        for m, v in zip(all_points_meta, vecs):
            if m["kind"] == KIND_TILE:
                pid = _point_id(f"{m['doc']}:p{m['page']}:t{m['row']}_{m['col']}")
            else:
                pid = _point_id(f"{m['doc']}:p{m['page']}:thumb")
                
            points.append(VisualPoint(
                point_id=pid, doc=m["doc"], page=m["page"], kind=m["kind"],
                row=m["row"], col=m["col"], vector=v, payload=m
            ))
            
        index.upsert(points)
        el = time.time() - t0
        
        # Memory Hygiene su MPS: svuota cache e logga uso swap ogni 10 batch
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            # PYTORCH_MPS_HIGH_WATERMARK_RATIO aiuta a limitare la saturazione della memoria unificata
            if (start // BATCH) % 10 == 0:
                torch.mps.empty_cache()
                try:
                    import subprocess
                    swap = subprocess.check_output(["sysctl", "-n", "vm.swapusage"], text=True).strip()
                    print(f"  [mem] swap: {swap}")
                except Exception:
                    pass

        
        # update progress per page
        successful = get_successful_pages(batch, failed_renders)
        for ref, pno in successful:
            stem = Path(ref).stem
            progress.write(json.dumps({"doc": stem, "page": pno, "ok": True}) + "\n")
            progress.flush()
            
        done += len(successful)
        if done % 20 < BATCH:
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9
            tot = time.time() - t_start
            print(f"[visual] {done}/{len(todo)} {tot / max(done, 1):.2f} s/pag RSS_picco={rss:.1f} GB", flush=True)

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9
    count = client.count("aike_pages_visual", exact=True).count
    print(f"[visual] FINE: ok={done} falliti={failed} punti_in_collection={count} tempo={(time.time() - t_start) / 60:.1f} min RSS_picco={rss:.2f} GB")
    return 1 if failed else 0

if __name__ == "__main__":
    sys.exit(main())
