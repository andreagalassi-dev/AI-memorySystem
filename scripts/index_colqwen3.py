"""Indicizzazione visiva ColQwen3 delle pagine route_colpali (MaxSim).

Anti-OOM: pipeline pagina-per-pagina (render DPI 150 cap 1540px -> embed batch
BATCH immagini con no_grad -> upsert wait=True -> free), checkpoint/resume via
_progress_colqwen3.jsonl (append+flush per pagina), picco RSS riportato a fine corsa.
I render PNG restano su disco (asset per la UI futura).
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
import uuid
import gc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
from PIL import Image  # noqa: E402

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.retrieval.colqwen3 import (  # noqa: E402
    MAX_SIDE_PX,
    RENDER_DPI,
    VISUAL_COLLECTION_COLQWEN3,
    embed_pages,
    ensure_visual_collection_colqwen3,
)

ROUTE = Path(os.environ.get(
    "VISUAL_ROUTE",
    ROOT / "output" / "spike" / "page_classes" / "_route_colpali.txt"))
RENDERS = ROOT / "output" / "visual" / "renders"
PROGRESS = ROOT / "output" / "visual" / "_progress_colqwen3.jsonl"
CORPUS = ROOT / "corpus"
BATCH = 2
LIMIT = int(os.environ.get("VISUAL_LIMIT", "0"))
# MPS hygiene
MPS_EMPTY_CACHE_EVERY = 4
TELEMETRY_EVERY = 20


def page_id(doc: str, page: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"visual_colqwen3/{doc}/p{page}"))


def render_page(pdf_path: Path, pno: int, out_png: Path) -> Image.Image:
    doc = fitz.open(pdf_path)
    page = doc[pno - 1]
    zoom = RENDER_DPI / 72
    rect = page.rect
    scale = min(zoom, MAX_SIDE_PX / max(rect.width, rect.height))
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pix.save(out_png)
    doc.close()
    return Image.open(out_png).convert("RGB")


def print_telemetry(done_count: int, total: int):
    import psutil
    process = psutil.Process(os.getpid())
    rss_mb = process.memory_info().rss / (1024 * 1024)
    swap_info = ""
    try:
        swap = psutil.swap_memory()
        swap_info = f", Swap Used: {swap.used / (1024*1024):.0f}MB"
    except Exception:
        pass
    print(f"[{done_count}/{total}] Telemetry -> RSS: {rss_mb:.0f}MB{swap_info}")


def main() -> int:
    from qdrant_client import models
    import torch

    refs = []
    for line in ROUTE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ref, p = line.rsplit("#p", 1)
            refs.append((ref, int(p)))
    client = get_qdrant_client()
    
    # decide_recreate parameter or VISUAL_RECREATE
    recreate = os.environ.get("VISUAL_RECREATE") == "1"
    ensure_visual_collection_colqwen3(client, recreate=recreate)
    
    done_ids = set()
    if PROGRESS.exists() and not recreate:
        for line in PROGRESS.open(encoding="utf-8"):
            try:
                e = json.loads(line)
                if e.get("ok"):
                    done_ids.add((e["doc"], e["page"]))
            except json.JSONDecodeError:
                pass

    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if recreate else "a"
    progress = PROGRESS.open(mode, encoding="utf-8")
    
    todo = [(r, p) for r, p in refs if (Path(r).stem, p) not in done_ids]
    print(f"[visual_colqwen3] pagine totali={len(refs)} da fare={len(todo)} "
          f"(resume: {len(refs) - len(todo)} gia' indicizzate)")

    t_start = time.time()
    done = failed = 0
    total_todo = len(todo)
    
    for start in range(0, total_todo, BATCH):
        if LIMIT and done >= LIMIT:
            break
            
        batch = todo[start:start + BATCH]
        images, meta = [], []
        
        for ref, pno in batch:
            stem = Path(ref).stem
            png = RENDERS / stem / f"p{pno:04d}.png"
            try:
                images.append(render_page(CORPUS / ref, pno, png))
                meta.append({"doc": stem, "cat": ref.split("/")[0], "page": pno,
                             "render": str(png.relative_to(ROOT))})
            except Exception as e:
                failed += 1
                progress.write(json.dumps({"ok": False, "doc": stem, "page": pno,
                                           "error": str(e)}) + "\n")
                progress.flush()
                print(f"Errore render {stem} p{pno}: {e}", file=sys.stderr)
        
        if not images:
            continue

        try:
            embs = embed_pages(images)
            pts = []
            for (ref, pno), emb, m in zip(batch, embs, meta):
                pts.append(models.PointStruct(
                    id=page_id(Path(ref).stem, pno),
                    vector=emb,
                    payload=m,
                ))
            client.upsert(VISUAL_COLLECTION_COLQWEN3, points=pts, wait=True)
            for m in meta:
                progress.write(json.dumps({"ok": True, "doc": m["doc"],
                                           "page": m["page"]}) + "\n")
            progress.flush()
            done += len(images)
            
            if done % TELEMETRY_EVERY == 0 or done == total_todo:
                print_telemetry(done, total_todo)
                
            if done % MPS_EMPTY_CACHE_EVERY == 0:
                if torch.backends.mps.is_available():
                    torch.mps.empty_cache()
                gc.collect()

        except Exception as e:
            failed += len(images)
            print(f"Errore UPSERT batch {start}: {e}", file=sys.stderr)
            for m in meta:
                progress.write(json.dumps({"ok": False, "doc": m["doc"],
                                           "page": m["page"], "error": str(e)}) + "\n")
            progress.flush()

    t_el = time.time() - t_start
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_mb = rss / (1024 * 1024 if sys.platform == "darwin" else 1024)
    print(f"[visual_colqwen3] Finito. ok={done} falliti={failed} in {t_el:.1f}s. "
          f"Picco RSS={rss_mb:.0f}MB.")
    
    # Verifica storage e conteggio punti
    try:
        col_info = client.get_collection(VISUAL_COLLECTION_COLQWEN3)
        print(f"Collection Info: Points count={col_info.points_count}")
    except Exception as e:
        print(f"Impossibile recuperare info collection: {e}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
