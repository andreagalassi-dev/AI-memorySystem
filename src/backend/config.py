"""Configurazione backend (Fase 0 -> Fase 1). Valori da variabili d'ambiente / .env.

I default puntano al setup locale (localhost). Nel container il valore
QDRANT_HOST viene sovrascritto a 'qdrant' (nome servizio sulla rete aike_net).

Fase 1 (BLOCCO 2): i modelli sono allineati a PIANO-IMPLEMENTAZIONE.md —
Gemma 4 via MLX (logica), GLM-OCR (estrazione tabelle), Qwen3-VL (PixelRAG).
Il context window e' clampato a local_ctx_cap per contenere la KV cache sui
32GB di RAM unificata. Ollama resta come legacy transizionale del grafo P3.4.
"""

from __future__ import annotations

import os
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Qdrant ---
    qdrant_host: str = "localhost"  # env QDRANT_HOST; nel container: "qdrant"
    qdrant_port: int = 6333  # env QDRANT_PORT (REST)
    qdrant_api_key: str | None = None
    qdrant_timeout_s: float = 5.0

    # --- PostgreSQL (registro versioni P4.1) ---
    postgres_host: str = "localhost"  # env POSTGRES_HOST; nel container: "postgres"
    postgres_port: int = 5432
    postgres_user: str = "aike"
    postgres_password: str = "aike_dev"  # dev-only; override da .env
    postgres_db: str = "aike_registry"
    # Fase 1.3 — ruolo SELECT-only per il ramo STRUCTURED (difesa in profondita').
    postgres_ro_user: str = "aike_ro"  # env POSTGRES_RO_USER
    postgres_ro_password: str = "aike_ro_dev"  # dev-only; override da .env

    # --- Modelli (Fase 1, allineato a PIANO-IMPLEMENTAZIONE.md) ---
    # Modello di ragionamento primario. Modificare con override in test o run specifici.
    # Alternativa testata supportata: "mlx-community/Nemotron-Cascade-2-30B-A3B-4bit"
    logic_model: str = os.getenv("AIKE_LOGIC_MODEL", "mlx-community/gemma-4-26b-a4b-it-4bit")
    # Estrazione visiva tabelle: GLM-OCR (zai-org/GLM-OCR, ~2GB BF16, 1B params).
    # Standard transformers (AutoModelForImageTextToText), no custom_code, MPS-safe.
    extraction_model: str = "zai-org/GLM-OCR"
    # Retrieval visivo: PixelRAG con Qwen3-VL-Embedding (su Qdrant, vedi retrieval/).
    # Cap duro sul context window dell'LLM: limita la KV cache (~2-4GB) e lascia
    # RAM libera per gli embedder residenti. Clampato dal ModelMultiplexer.
    local_ctx_cap: int = 16000

    # --- Ollama Cervello (Fase 1, Produzione) ---
    brain_model: str = "gemma4:26b-mlx"
    brain_think: bool = False

    # --- Ollama fallback ---
    # Ollama resta ESCLUSIVAMENTE come fallback di transizione nel caso in cui
    # MLXModel fallisca o mlx-lm non sia disponibile.
    # Comando di download: ollama pull gemma4:26b-a4b-it-q4
    # Alternativa testata: nemotron-cascade-2:30b-a3b-q4
    ollama_host: str = "http://localhost:11434"  # env OLLAMA_HOST
    ollama_model: str = "qwen2.5:14b"  # fallback model

    # --- Provider LLM alternativi (API Cloud / Mock) ---
    # provider: 'auto', 'ollama', 'openai', 'gemini', 'anthropic', 'mock'
    llm_provider: str = os.getenv("AIKE_LLM_PROVIDER", "auto")
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

    anthropic_api_key: str | None = os.getenv("ANTHROPIC_API_KEY")
    anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")

    # --- Backend ---
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000


settings = Settings()
