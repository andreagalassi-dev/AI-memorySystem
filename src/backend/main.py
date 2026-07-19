"""App backend FastAPI (Fase 0).

Espone /health che verifica anche la raggiungibilita' di Qdrant: non basta
"backend ok", il Vector DB deve rispondere. Se Qdrant e' giu' -> HTTP 503.

Avvio locale:
    uv run uvicorn src.backend.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from src.backend.clients.qdrant_client import get_qdrant_client

app = FastAPI(title="AI Knowledge Engine — Backend", version="0.0.0")


@app.get("/health")
def health() -> JSONResponse:
    """Health check con ping a Qdrant e stato rami (probe leggero, non e2e).

    Verifica la raggiungibilita' di Qdrant, le connessioni ai DB (Postgres)
    e lo stato dei rami (structured, visual) senza caricare i modelli pesanti.
    """
    from src.backend.config import settings

    result: dict[str, object] = {
        "backend": "ok",
        "narrative": "ok",
        "brain_model": settings.brain_model,
        "fallback_model": settings.ollama_model,
        "resident_model": None,
    }

    try:
        client = get_qdrant_client()
        collections = client.get_collections()
        result["qdrant"] = "ok"
        result["qdrant_collections"] = len(collections.collections)
    except Exception as exc:  # noqa: BLE001
        result["qdrant"] = "unreachable"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(status_code=503, content=result)
        
    try:
        from src.backend.clients.postgres_client import get_pg_conn_readonly
        conn = get_pg_conn_readonly()
        conn.close()
        result["structured"] = "ok"
    except Exception as exc:
        result["structured"] = f"degraded: {type(exc).__name__} ({exc})"

    try:
        from src.retrieval.pixelrag.index import DEFAULT_VISUAL_COLLECTION
        from src.retrieval.pixelrag.embedder import Qwen3VLEmbedder # imported as check
        if not client.collection_exists(DEFAULT_VISUAL_COLLECTION):
            raise RuntimeError(f"collection {DEFAULT_VISUAL_COLLECTION} not found")
        result["visual"] = "ok"
    except Exception as exc:
        result["visual"] = f"degraded: {type(exc).__name__} ({exc})"

    return JSONResponse(status_code=200, content=result)
