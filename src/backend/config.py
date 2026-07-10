"""Configurazione backend (Fase 0 -> Fase 1). Valori da variabili d'ambiente / .env.

I default puntano al setup locale (localhost). Nel container il valore
QDRANT_HOST viene sovrascritto a 'qdrant' (nome servizio sulla rete aike_net).

Fase 1 (BLOCCO 2): i modelli sono allineati a PIANO-IMPLEMENTAZIONE.md —
Gemma 4 via MLX (logica), GLM-OCR (estrazione tabelle), Qwen3-VL (PixelRAG).
Il context window e' clampato a local_ctx_cap per contenere la KV cache sui
32GB di RAM unificata. Ollama resta come legacy transizionale del grafo P3.4.
"""

from __future__ import annotations

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
    # Cervello / ragionatore: Google Gemma 4 12B servito via MLX da HuggingFace
    # (non Ollama). Caricato/scaricato dal ModelMultiplexer (src/serving).
    logic_model: str = "google/gemma-4-12b-it"
    # Estrazione visiva tabelle: GLM-OCR (zai-org/GLM-OCR, ~2GB BF16, 1B params).
    # Standard transformers (AutoModelForImageTextToText), no custom_code, MPS-safe.
    extraction_model: str = "zai-org/GLM-OCR"
    # Retrieval visivo: PixelRAG con Qwen3-VL-Embedding (su Qdrant, vedi retrieval/).
    # Cap duro sul context window dell'LLM: limita la KV cache (~2-4GB) e lascia
    # RAM libera per gli embedder residenti. Clampato dal ModelMultiplexer.
    local_ctx_cap: int = 16000

    # --- Ollama legacy (transizionale) ---
    # Usato ancora dal client Ollama (src/backend/clients/ollama_client.py) per il
    # grafo P3.4 in attesa del drop-in dell'adapter MLX sul Port LanguageModel.
    # NON e' piu' il model di logica (ora logic_model via MLX). Verra' rimosso a
    # completamento del BLOCCO 3, quando il runner massivo usera' il Multiplexer.
    ollama_host: str = "http://localhost:11434"  # env OLLAMA_HOST
    ollama_model: str = "qwen2.5:14b"  # legacy grafo P3.4 (non logica)

    # --- Backend ---
    backend_host: str = "0.0.0.0"
    backend_port: int = 8000


settings = Settings()
