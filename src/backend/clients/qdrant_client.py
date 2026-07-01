"""Factory del client Qdrant.

Il client si costruisce senza aprire subito la connessione: l'eventuale
irraggiungibilita' emerge alla prima operazione (es. get_collections), dove
viene gestita dal chiamante (endpoint /health -> HTTP 503).
"""

from __future__ import annotations

from functools import lru_cache

from qdrant_client import QdrantClient

from src.backend.config import settings


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """Ritorna un QdrantClient condiviso, configurato da env (host/port)."""
    return QdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        api_key=settings.qdrant_api_key or None,
        timeout=settings.qdrant_timeout_s,
    )
