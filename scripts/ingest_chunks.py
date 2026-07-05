"""P2.3 — Ingestion dual-index dei chunk-riga in Qdrant (dense BGE-M3 + sparse BM25).

Gira nell'env di PROGETTO (niente PEP 723: FlagEmbedding/torch sono dipendenze di
progetto, condivise col backend). Prerequisiti: `uv sync`, qdrant su
(`docker compose up -d qdrant`), chunk generati (`uv run scripts/chunk_tables.py`).

Sequenza fail-fast (exit 1 al primo check rosso):
  [1/7] input chunks.jsonl (173 attesi, chunk_id univoci)
  [2/7] modelli caricati (dense dim == 1024 su encode di prova)
  [3/7] collezione ricreata: named dense(1024,cosine) + sparse(IDF) + payload index
  [4/7] upsert completo: count == n chunk
  [5/7] canale lessicale: codice esatto -> top-1; codice condiviso -> tutti gli hit
  [6/7] canale denso: query naturale -> desc pertinente nei top-5 (smoke)
  [7/7] ibrida RRF: il punto del check 5 presente nei top-3

La collezione e' dato DERIVATO (drop+create a ogni run): la sorgente di verita'
e' chunks.jsonl. Run idempotente per costruzione. Il primo avvio scarica BGE-M3
(~2.3 GB) nella cache HuggingFace.

Uso: uv run scripts/ingest_chunks.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdrant_client import models  # noqa: E402

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.ingestion.embedding import (  # noqa: E402
    BM25_LANGUAGE,
    COLLECTION,
    DENSE_DIM,
    DENSE_NAME,
    SPARSE_NAME,
    dense_text,
    embed_dense,
    embed_sparse_docs,
    embed_sparse_query,
    point_id,
    sparse_text,
)

CHUNKS_DIR = ROOT / "output" / "spike" / "chunks"
TABLE_IDS = ["MAMOLI_P231_IT", "MAMOLI_P060_IT",
             "DELITOOLS_EN", "ELECTROLUX_EN", "ELECTROLUX_ES"]
EXPECTED_SPIKE = 237  # 92+84 (Mamoli P2.2 + 3 riparati debito §4) + 19+21+21 (CSV XLSX P2.4)
# P2.6: chunk del corpus esteso (54 PDF via Docling + profili brand).
CORPUS_CHUNKS_DIR = ROOT / "output" / "ingestion" / "chunks"
UPSERT_BATCH = 64
EXACT_CODE = "V09156800007"   # canale lessicale, worst-case P231
SHARED_CODE = "11282"         # codice corto ripetuto su piu' blocchi P060
DENSE_QUERY = "deviatore cromato"

TOTAL = 7
_step = 0


def check(desc: str, ok: bool, detail: str = "") -> None:
    global _step
    _step += 1
    print(f"[{_step}/{TOTAL}] {desc}: {'OK' if ok else 'FAIL'}"
          f"{' — ' + detail if detail else ''}")
    if not ok:
        raise SystemExit(1)


def main() -> int:
    # -- [1/7] input ---------------------------------------------------------
    chunks: list[dict] = []
    for tid in TABLE_IDS:
        path = CHUNKS_DIR / tid / "chunks.jsonl"
        if not path.exists():
            check("Input chunks.jsonl", False,
                  f"{path} assente: eseguire scripts/chunk_tables.py")
        chunks.extend(json.loads(line) for line in path.open(encoding="utf-8"))
    n_spike = len(chunks)
    n_corpus = 0
    if CORPUS_CHUNKS_DIR.exists():
        for jl in sorted(CORPUS_CHUNKS_DIR.glob("*.jsonl")):
            if jl.name.startswith("_"):
                continue
            for line in jl.open(encoding="utf-8"):
                chunks.append(json.loads(line))
                n_corpus += 1
    ids = [c["chunk_id"] for c in chunks]
    check("Input chunks (spike + corpus)",
          n_spike == EXPECTED_SPIKE and len(ids) == len(set(ids)),
          f"spike={n_spike} corpus={n_corpus} totale={len(chunks)}, "
          f"chunk_id univoci={len(set(ids))}")

    # -- [2/7] modelli --------------------------------------------------------
    probe = embed_dense(["prova di caricamento"])
    sparse_probe = embed_sparse_docs(["prova 14R"])
    check("Modelli caricati", len(probe[0]) == DENSE_DIM and len(sparse_probe) == 1,
          f"dense dim={len(probe[0])}, sparse bm25 lingua={BM25_LANGUAGE}")

    # -- [3/7] collezione -----------------------------------------------------
    client = get_qdrant_client()
    if client.collection_exists(COLLECTION):
        client.delete_collection(COLLECTION)
    client.create_collection(
        collection_name=COLLECTION,
        vectors_config={DENSE_NAME: models.VectorParams(
            size=DENSE_DIM, distance=models.Distance.COSINE)},
        sparse_vectors_config={SPARSE_NAME: models.SparseVectorParams(
            modifier=models.Modifier.IDF)},
    )
    for field in ("code", "lang", "doc", "status"):
        client.create_payload_index(COLLECTION, field_name=field,
                                    field_schema=models.PayloadSchemaType.KEYWORD)
    info = client.get_collection(COLLECTION)
    cfg = info.config.params
    check("Collezione ricreata (dense 1024/cosine + sparse IDF + index payload)",
          cfg.vectors[DENSE_NAME].size == DENSE_DIM
          and cfg.sparse_vectors[SPARSE_NAME].modifier == models.Modifier.IDF,
          COLLECTION)

    # -- [4/7] embedding + upsert a lotti -------------------------------------
    d_texts = [dense_text(c) for c in chunks]
    with_dense = [i for i, t in enumerate(d_texts) if t]
    dense_vecs = embed_dense([d_texts[i] for i in with_dense])
    dense_by_idx = dict(zip(with_dense, dense_vecs))
    sparse_vecs = embed_sparse_docs([sparse_text(c) for c in chunks])

    points = []
    for i, c in enumerate(chunks):
        vector: dict = {SPARSE_NAME: models.SparseVector(
            indices=sparse_vecs[i].indices.tolist(),
            values=sparse_vecs[i].values.tolist())}
        if i in dense_by_idx:
            vector[DENSE_NAME] = dense_by_idx[i]
        payload = dict(c, desc_search=d_texts[i])
        points.append(models.PointStruct(id=point_id(c["chunk_id"]),
                                         vector=vector, payload=payload))
    for start in range(0, len(points), UPSERT_BATCH):
        client.upsert(COLLECTION, points=points[start:start + UPSERT_BATCH], wait=True)
    count = client.count(COLLECTION, exact=True).count
    check("Upsert completo", count == len(chunks),
          f"count={count}, senza vettore denso={len(chunks) - len(with_dense)}")

    # -- [5/7] canale lessicale -----------------------------------------------
    q = embed_sparse_query(EXACT_CODE)
    sv = models.SparseVector(indices=q.indices.tolist(), values=q.values.tolist())
    hits = client.query_points(COLLECTION, query=sv, using=SPARSE_NAME,
                               with_payload=True, limit=3).points
    top = hits[0].payload["code"] if hits else None
    ok_exact = top == EXACT_CODE

    q2 = embed_sparse_query(SHARED_CODE)
    sv2 = models.SparseVector(indices=q2.indices.tolist(), values=q2.values.tolist())
    hits2 = client.query_points(COLLECTION, query=sv2, using=SPARSE_NAME,
                                with_payload=True, limit=10).points
    codes2 = [h.payload["code"] for h in hits2]
    ok_shared = len(hits2) >= 2 and all(c == SHARED_CODE for c in codes2)
    check(f"Canale lessicale ({EXACT_CODE} top-1; {SHARED_CODE} tutti gli hit)",
          ok_exact and ok_shared,
          f"top-1={top}; {SHARED_CODE}: {len(hits2)} hit tutti uguali={ok_shared}")

    # -- [6/7] canale denso ----------------------------------------------------
    dq = embed_dense([DENSE_QUERY])[0]
    hits = client.query_points(COLLECTION, query=dq, using=DENSE_NAME,
                               with_payload=True, limit=5).points
    descs = [(h.payload.get("desc_search") or "") for h in hits]
    ok_dense = any("deviator" in d for d in descs)
    check(f"Canale denso ('{DENSE_QUERY}' -> desc pertinente nei top-5)", ok_dense,
          f"top-5 desc: {[d[:40] for d in descs]}")

    # -- [7/7] ibrida RRF -------------------------------------------------------
    hy = client.query_points(
        COLLECTION,
        prefetch=[
            models.Prefetch(query=embed_dense([EXACT_CODE])[0],
                            using=DENSE_NAME, limit=5),
            models.Prefetch(query=sv, using=SPARSE_NAME, limit=5),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        with_payload=True, limit=3,
    ).points
    codes = [h.payload["code"] for h in hy]
    check("Ibrida RRF (prefetch dense+sparse, codice esatto nei top-3)",
          EXACT_CODE in codes, f"top-3={codes}")

    print(f"\nP2.3 ingestion: TUTTI I CHECK VERDI — {count} punti in '{COLLECTION}'.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
