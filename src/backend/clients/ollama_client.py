"""Factory del client Ollama (LLM locale, ragionatore solo-testo P3.4).

Come il client Qdrant: non apre subito la connessione; l'irraggiungibilita'
emerge alla prima chiamata. Opzioni di default per SINTESI SICURA:
temperatura bassa e seed fisso (riproducibilita' nello spike).
"""

from __future__ import annotations

from functools import lru_cache

from ollama import Client

from src.backend.config import settings

# Sintesi ancorata: quasi-greedy + seed fisso -> output stabile e verificabile.
SAFE_OPTIONS = {"temperature": 0.1, "seed": 7, "num_predict": 512}


@lru_cache(maxsize=1)
def get_ollama_client() -> Client:
    return Client(host=settings.ollama_host)


def chat(client: Client, system: str, prompt: str, *, model: str | None = None,
         options: dict | None = None) -> str:
    """Una risposta del modello a (system, prompt). Ritorna il solo testo."""
    resp = client.chat(
        model=model or settings.ollama_model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": prompt}],
        options=options or SAFE_OPTIONS,
    )
    return resp["message"]["content"]
