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

    # --- PostgreSQL (registro versioni P4.1) ---
    postgres_host: str = "localhost"     # env POSTGRES_HOST; nel container: "postgres"
    postgres_port: int = 5432
    postgres_user: str = "aike"
    postgres_password: str = "aike_dev"  # dev-only; override da .env
    postgres_db: str = "aike_registry"

    # --- Ollama (LLM locale, ragionatore solo-testo P3.4) ---
    ollama_host: str = "http://localhost:11434"  # env OLLAMA_HOST
    ollama_model: str = "qwen2.5:14b"            # 14B default (32B solo piano-B)

    # --- Backend ---
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000


settings = Settings()
