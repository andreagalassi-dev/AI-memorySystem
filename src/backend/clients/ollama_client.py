"""Factory del client Ollama (LLM locale, ragionatore solo-testo P3.4).

Come il client Qdrant: non apre subito la connessione; l'irraggiungibilita'
emerge alla prima chiamata. Opzioni di default per SINTESI SICURA:
temperatura bassa e seed fisso (riproducibilita' nello spike).
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from ollama import Client

from src.backend.config import settings

# Sintesi ancorata: quasi-greedy + seed fisso -> output stabile e verificabile.
SAFE_OPTIONS = {"temperature": 0.1, "seed": 7, "num_predict": 512}


@lru_cache(maxsize=1)
def get_ollama_client() -> Client:
    return Client(host=settings.ollama_host)


def chat(client: Client, system: str, prompt: str, *, model: str | None = None,
         options: dict | None = None, think: bool | None = None) -> str:
    """Una risposta del modello a (system, prompt). Ritorna il solo testo."""
    kwargs = {
        "model": model or settings.ollama_model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
        "options": options or SAFE_OPTIONS,
    }
    if think is not None:
        kwargs["think"] = think
        
    resp = client.chat(**kwargs)
    return resp["message"]["content"]


class OllamaModel:
    """Adapter `LanguageModel` (Fase 1.3) sul reasoner locale Ollama.

    Accende il ramo STRUCTURED: `render_node` gli passa `SYSTEM_RENDER` + i dati XML e
    riceve il testo formattato. Soft-defense = decoding deterministico (`SAFE_OPTIONS`:
    temperatura bassa + seed fisso), che massimizza l'aderenza ai `<dati>`; le difese
    DURE (guardrail codici, value-grounding prezzi) restano a valle nel grafo.

    Client e opzioni sono LAZY (come `Qwen3VLEmbedder`): costruire l'adapter non apre
    connessioni. `_chat` e' iniettabile nei test (DB/Ollama-free). Fail-closed: gli
    errori del modello NON vengono inghiottiti — risalgono a `render_node` che degrada
    a `system_error`. Backend intercambiabile: l'MLXModel sara' un drop-in sullo stesso
    Port `LanguageModel`, zero impatto sul grafo."""

    def __init__(self, *, model: str | None = None, options: dict | None = None,
                 think: bool | None = None,
                 _chat: Callable[[str, str], str] | None = None):
        self._model = model
        self._options = options or SAFE_OPTIONS
        self._think = think
        self._chat = _chat

    def generate(self, system: str, user: str) -> str:
        if self._chat is None:
            client = get_ollama_client()  # lazy: connessione alla prima chiamata
            self._chat = lambda s, u: chat(client, s, u, model=self._model,
                                           options=self._options, think=self._think)
        return self._chat(system, user)
