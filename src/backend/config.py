"""Configurazione backend (Fase 0). Valori da variabili d'ambiente / .env.

I default puntano al setup locale (localhost). Nel container il valore
QDRANT_HOST viene sovrascritto a 'qdrant' (nome servizio sulla rete aike_net).
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Qdrant ---
    qdrant_host: str = "localhost"   # env QDRANT_HOST; nel container: "qdrant"
    qdrant_port: int = 6333          # env QDRANT_PORT (REST)
    qdrant_api_key: str | None = None
    qdrant_timeout_s: float = 5.0

    # --- Backend ---
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000


settings = Settings()
