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
    """Health check con ping a Qdrant.

    200 -> backend ok e Qdrant raggiungibile.
    503 -> Qdrant irraggiungibile (il backend gira ma la dipendenza critica e' giu').
    """
    result: dict[str, object] = {"backend": "ok"}
    try:
        client = get_qdrant_client()
        collections = client.get_collections()
        result["qdrant"] = "ok"
        result["qdrant_collections"] = len(collections.collections)
        return JSONResponse(status_code=200, content=result)
    except Exception as exc:  # noqa: BLE001 — qualsiasi errore = dipendenza non pronta
        result["qdrant"] = "unreachable"
        result["error"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(status_code=503, content=result)
