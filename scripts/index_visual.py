"""P2.7 — Indicizzazione visiva Qwen3-VL delle pagine route_colpali (MaxSim).

Anti-OOM: pipeline pagina-per-pagina (render DPI 150 cap 1540px -> embed batch
BATCH immagini con no_grad -> upsert wait=True -> free), checkpoint/resume via
_progress.jsonl (append+flush per pagina), picco RSS riportato a fine corsa.
I render PNG restano su disco (asset per la UI futura).

Env: VISUAL_LIMIT=N (pilota), VISUAL_RECREATE=1 (drop collection).
Uso: uv run scripts/index_visual.py
"""

from __future__ import annotations

import json
import os
import resource
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import fitz  # noqa: E402
from PIL import Image  # noqa: E402

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.retrieval.qwen3vl import (  # noqa: E402
    MAX_SIDE_PX,
    RENDER_DPI,
    VISUAL_COLLECTION,
    embed_pages,
    ensure_visual_collection,
)

ROUTE = Path(os.environ.get(
    "VISUAL_ROUTE",
    ROOT / "output" / "spike" / "page_classes" / "_route_colpali.txt"))
RENDERS = ROOT / "output" / "visual" / "renders"
PROGRESS = ROOT / "output" / "visual" / "_progress.jsonl"
CORPUS = ROOT / "corpus"
BATCH = 2
LIMIT = int(os.environ.get("VISUAL_LIMIT", "0"))


def page_id(doc: str, page: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"visual/{doc}/p{page}"))


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


def main() -> int:
    from qdrant_client import models

    refs = []
    for line in ROUTE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            ref, p = line.rsplit("#p", 1)
            refs.append((ref, int(p)))
    client = get_qdrant_client()
    ensure_visual_collection(client,
                             recreate=os.environ.get("VISUAL_RECREATE") == "1")
    done_ids = set()
    if PROGRESS.exists():
        for line in PROGRESS.open(encoding="utf-8"):
            e = json.loads(line)
            if e.get("ok"):
                done_ids.add((e["doc"], e["page"]))

    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    progress = PROGRESS.open("a", encoding="utf-8")
    todo = [(r, p) for r, p in refs if (Path(r).stem, p) not in done_ids]
    print(f"[visual] pagine totali={len(refs)} da fare={len(todo)} "
          f"(resume: {len(refs) - len(todo)} gia' indicizzate)")

    t_start = time.time()
    done = failed = 0
    for start in range(0, len(todo), BATCH):
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
            except Exception as exc:
                progress.write(json.dumps({"doc": stem, "page": pno, "ok": False,
                                           "error": str(exc)}) + "\n")
                progress.flush()
                failed += 1
        if not images:
            continue
        t0 = time.time()
        try:
            vecs = embed_pages(images)
        except Exception as exc:
            for m in meta:
                progress.write(json.dumps({**m, "ok": False,
                                           "error": f"embed: {exc}"}) + "\n")
                progress.flush()
            failed += len(meta)
            continue
        points = [models.PointStruct(id=page_id(m["doc"], m["page"]),
                                     vector=v, payload=m)
                  for m, v in zip(meta, vecs)]
        client.upsert(VISUAL_COLLECTION, points=points, wait=True)
        el = time.time() - t0
        for m, v in zip(meta, vecs):
            progress.write(json.dumps({**m, "ok": True, "patches": len(v),
                                       "seconds": round(el / len(meta), 2)})
                           + "\n")
            progress.flush()
        done += len(meta)
        for im in images:
            im.close()
        if done % 20 < BATCH:
            rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9
            tot = time.time() - t_start
            print(f"[visual] {done}/{len(todo)} "
                  f"{tot / max(done, 1):.2f} s/pag  RSS_picco={rss:.1f} GB",
                  flush=True)

    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9
    count = client.count(VISUAL_COLLECTION, exact=True).count
    print(f"[visual] FINE: ok={done} falliti={failed} "
          f"punti_in_collection={count} "
          f"tempo={(time.time() - t_start) / 60:.1f} min RSS_picco={rss:.2f} GB")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
