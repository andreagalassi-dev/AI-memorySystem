# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "qdrant-client>=1.18,<2.0",
# ]
# ///
"""P2.1 — Connection check di Qdrant per il retrieval IBRIDO (dense + sparse).

Non e' un semplice ping: valida che il server locale supporti ESATTAMENTE la
configurazione di collezione che P2.3 usera' per il dual-indexing, con vettori
sintetici (niente modelli di embedding: la qualita' semantica non e' in scope qui):

  [1/6] Health + versione server (atteso il pin del compose, v1.18.x)
  [2/6] Create collection usa-e-getta: named dense (1024, cosine, dim BGE-M3)
        + named sparse con modifier IDF (scoring BM25-style)
  [3/6] Upsert duale di 3 punti con payload di citazione (codice/doc/pagina/lingua)
  [4/6] Query canale LESSICALE (sparse, indice derivato dal codice esatto) -> top-1
  [5/6] Query canale SEMANTICO (dense) -> top-1
  [6/6] Query IBRIDA (prefetch dense+sparse, fusion RRF: l'API di P2.4) -> atteso in top-k

Teardown garantito (try/finally): la collezione di test viene sempre cancellata,
anche in caso di fallimento. Rilanciabile senza stato residuo.

Uso:
    docker compose up -d qdrant   # prerequisito, lo script NON avvia container
    uv run scripts/check_qdrant_hybrid.py

Exit code: 0 = tutti i check verdi; 1 = primo check rosso (fail-fast).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import zlib

from qdrant_client import QdrantClient, models

QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")
EXPECTED_SERVER_MINOR = "1.18"  # pin nel docker-compose: qdrant/qdrant:v1.18.2
DENSE_DIM = 1024  # dimensione BGE-M3: la config di P2.3 deve poter nascere identica
DENSE_NAME = "dense"
SPARSE_NAME = "sparse"

# Codici canonici reali del corpus (Mamoli p231, output P1.5+): il payload del
# chunk-riga che P2.3 indicizzera' davvero.
POINTS_SPEC = [
    {"code": "V09156800007", "doc": "MAMOLI_P231_IT", "page": 231, "lang": "IT"},
    {"code": "V09169700C01", "doc": "MAMOLI_P231_IT", "page": 231, "lang": "IT"},
    {"code": "V09202000C01", "doc": "MAMOLI_P231_IT", "page": 231, "lang": "IT"},
]
QUERY_CODE = "V09156800007"  # il check lessicale deve riportare QUESTO al top-1
DENSE_QUERY_TARGET = 1  # indice del punto atteso al top-1 del check semantico

TOTAL = 6
_step = 0


def check(desc: str, ok: bool, detail: str = "") -> None:
    """Logga il check numerato; su fallimento esce (il teardown e' nel finally)."""
    global _step
    _step += 1
    status = "OK" if ok else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"[{_step}/{TOTAL}] {desc}: {status}{suffix}")
    if not ok:
        raise SystemExit(1)


def http_get_json(path: str, timeout: float = 5.0) -> dict:
    with urllib.request.urlopen(f"{QDRANT_URL}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def code_to_sparse_index(code: str) -> int:
    """Indice sparse deterministico dal codice ricambio (CRC32, stabile tra run).

    Simula il termine lessicale esatto: in P2.3 l'indice verra' dal tokenizer
    sparse reale, qui serve solo una dimensione univoca e riproducibile per codice.
    """
    return zlib.crc32(code.encode("utf-8"))


def basis_dense(i: int) -> list[float]:
    """Vettori densi sintetici massimamente separati (base canonica)."""
    v = [0.0] * DENSE_DIM
    v[i] = 1.0
    return v


def main() -> int:
    # -- [1/6] health + versione -------------------------------------------------
    try:
        with urllib.request.urlopen(f"{QDRANT_URL}/healthz", timeout=5) as resp:
            healthz = resp.read().decode().strip()
        root = http_get_json("/")
    except (urllib.error.URLError, OSError) as exc:
        check(
            "Health server Qdrant",
            False,
            f"{QDRANT_URL} irraggiungibile ({exc}); esegui `docker compose up -d qdrant`",
        )
        return 1  # non raggiunto: check() esce gia'
    version = root.get("version", "?")
    if not version.startswith(EXPECTED_SERVER_MINOR):
        print(f"  ATTENZIONE: versione server {version} != pin atteso {EXPECTED_SERVER_MINOR}.x")
    check("Health server Qdrant", bool(healthz), f"'{healthz}', versione {version}")

    client = QdrantClient(url=QDRANT_URL, timeout=10)
    collection = f"_p21_conncheck_{int(time.time())}"

    try:
        # -- [2/6] collezione dual-index (la config che P2.3 replichera') --------
        client.create_collection(
            collection_name=collection,
            vectors_config={
                DENSE_NAME: models.VectorParams(size=DENSE_DIM, distance=models.Distance.COSINE)
            },
            sparse_vectors_config={
                SPARSE_NAME: models.SparseVectorParams(modifier=models.Modifier.IDF)
            },
        )
        info = client.get_collection(collection)
        check(
            "Create collection dense(1024,cosine) + sparse(IDF)",
            info.status is not None,
            collection,
        )

        # -- [3/6] upsert duale ---------------------------------------------------
        points = [
            models.PointStruct(
                id=i,
                vector={
                    DENSE_NAME: basis_dense(i),
                    SPARSE_NAME: models.SparseVector(
                        indices=[code_to_sparse_index(spec["code"])], values=[1.0]
                    ),
                },
                payload=spec,
            )
            for i, spec in enumerate(POINTS_SPEC)
        ]
        client.upsert(collection_name=collection, points=points, wait=True)
        count = client.count(collection, exact=True).count
        check("Upsert duale di 3 punti (dense+sparse+payload)", count == 3, f"count={count}")

        # -- [4/6] canale lessicale: il codice esatto deve arrivare top-1 ---------
        hits = client.query_points(
            collection_name=collection,
            query=models.SparseVector(indices=[code_to_sparse_index(QUERY_CODE)], values=[1.0]),
            using=SPARSE_NAME,
            with_payload=True,
            limit=3,
        ).points
        top_code = hits[0].payload["code"] if hits else None
        check(
            f"Query lessicale (sparse) per codice {QUERY_CODE}",
            top_code == QUERY_CODE,
            f"top-1={top_code}, hits={len(hits)}",
        )

        # -- [5/6] canale semantico -----------------------------------------------
        hits = client.query_points(
            collection_name=collection,
            query=basis_dense(DENSE_QUERY_TARGET),
            using=DENSE_NAME,
            with_payload=True,
            limit=3,
        ).points
        expected = POINTS_SPEC[DENSE_QUERY_TARGET]["code"]
        top_code = hits[0].payload["code"] if hits else None
        check(
            "Query semantica (dense) sul vettore del punto atteso",
            top_code == expected,
            f"top-1={top_code}, atteso={expected}",
        )

        # -- [6/6] ibrida: prefetch dense+sparse, fusion RRF (l'API di P2.4) ------
        hits = client.query_points(
            collection_name=collection,
            prefetch=[
                models.Prefetch(query=basis_dense(0), using=DENSE_NAME, limit=3),
                models.Prefetch(
                    query=models.SparseVector(
                        indices=[code_to_sparse_index(QUERY_CODE)], values=[1.0]
                    ),
                    using=SPARSE_NAME,
                    limit=3,
                ),
            ],
            query=models.FusionQuery(fusion=models.Fusion.RRF),
            with_payload=True,
            limit=3,
        ).points
        codes = [h.payload["code"] for h in hits]
        check(
            "Query ibrida (prefetch dense+sparse, fusion RRF)",
            QUERY_CODE in codes,
            f"top-{len(codes)}={codes}",
        )
    finally:
        client.delete_collection(collection)
        leftover = client.collection_exists(collection)
        print(f"Teardown: collezione '{collection}' cancellata (residua: {leftover})")
        if leftover:
            print("  ATTENZIONE: la collezione di test non risulta cancellata.")

    print("P2.1 connection check: TUTTI I CHECK VERDI.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
