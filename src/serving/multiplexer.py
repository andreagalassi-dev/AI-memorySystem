"""Model Multiplexer / Watchdog per la gestione della memoria (Fase 1, BLOCCO 1).

Su un Mac M1 Pro 32GB la memoria unificata e' condivisa tra tutti i modelli: caricare
insieme GLM-OCR (~2GB), Qwen3-VL-Embedding e Gemma 4 12B (~8-12GB in Q4) porterebbe
a OOM e swap. Questo modulo garantisce tre invarianti architetturali:

1. EVICTION: prima di caricare un nuovo modello, gli inattivi vengono scaricati, in modo
   che mai piu' di `max_resident` pesi risiedano contemporaneamente (PIANO-IMPLEMENTAZIONE
   riga 6). Selezione vittima LRU.
2. CLAMPING: il context window viene clampato a `ctx_cap` (8-16K) per contenere la KV cache
   (~2-4GB) e lasciare RAM libera per gli embedder residenti (riga 26).
3. ANTI-CHURN: richiedere un modello gia' residente non provoca reload (idempotenza).

`ModelRuntime` e' il Port (runtime_checkable Protocol); `FakeModelRuntime` gira in memoria
per i test di invarianti; `MlxModelRuntime` e' l'adapter reale per Apple Silicon via
`mlx-lm` (import lazy: il modulo si importa anche senza mlx-lm installato, cosi' i test
architetturali restano veloci e privi di download).
"""

from __future__ import annotations

import gc
import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class ModelRuntime(Protocol):
    """Port del runtime di serving di un modello.

    L'adapter concreto decide COME caricare/scaricare (MLX, Ollama, vLLM...).
    Il Multiplexer decide QUANDO, applicando le invarianti di memoria.
    """

    def load(self, name: str, num_ctx: int) -> None:
        """Carica `name` in memoria con context window = num_ctx (gia' clampato)."""
        ...

    def unload(self, name: str) -> None:
        """Scarica `name` liberando pesi e KV cache. Fail-closed: se non residente, no-op."""
        ...

    def resident(self) -> set[str]:
        """Insieme (copia) dei nomi attualmente residenti in memoria."""
        ...


class FakeModelRuntime:
    """Runtime in memoria per i test di invarianti (TDD).

    Registra ogni chiamata in `calls` come tupla:
      ("load", name, num_ctx) | ("unload", name)
    """

    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self._resident: set[str] = set()

    def load(self, name: str, num_ctx: int) -> None:
        self.calls.append(("load", name, num_ctx))
        self._resident.add(name)

    def unload(self, name: str) -> None:
        self.calls.append(("unload", name))
        self._resident.discard(name)

    def resident(self) -> set[str]:
        return set(self._resident)


class ModelMultiplexer:
    """Watchdog: orchestra load/unload sul `ModelRuntime` garantendo le invarianti.

    Mantiene un ordinamento LRU interno (fonte di verita' sulle vittime di evizione);
    lo stato "residente" effettivo vive nel runtime, ma solo il Multiplexer invoca
    load/unload, quindi le due viste restano sincronizzate.
    """

    def __init__(
        self,
        runtime: ModelRuntime,
        max_resident: int = 1,
        ctx_cap: int = 16000,
    ) -> None:
        if max_resident < 1:
            raise ValueError("max_resident deve essere >= 1")
        if ctx_cap < 1:
            raise ValueError("ctx_cap deve essere >= 1")
        self.runtime = runtime
        self.max_resident = max_resident
        self.ctx_cap = ctx_cap
        self._lru: list[str] = []  # indice 0 = LRU (meno recente); coda = MRU

    def use(self, name: str, num_ctx: int | None = None) -> int:
        """Attiva `name` per l'uso; ritorna il num_ctx effettivo (clampato).

        - num_ctx None => usa ctx_cap come default.
        - num_ctx > ctx_cap => clampato a ctx_cap.
        """
        eff = self.ctx_cap if num_ctx is None else min(num_ctx, self.ctx_cap)

        # Invariante 3 (anti-churn): gia' residente => aggiorna solo LRU, nessun reload.
        if name in self._lru:
            self._lru.remove(name)
            self._lru.append(name)
            return eff

        # Invariante 1 (eviction): libera slot fino a stare sotto il cap.
        while len(self._lru) >= self.max_resident:
            victim = self._lru.pop(0)  # LRU
            self.runtime.unload(victim)
            logger.info("evicted %s per caricare %s", victim, name)

        self.runtime.load(name, eff)
        self._lru.append(name)
        return eff

    def resident(self) -> set[str]:
        """Vista (copia) dei modelli residenti secondo il runtime."""
        return self.runtime.resident()

    def unload_all(self) -> None:
        """Scarica tutto (shutdown pulito, usato a fine run massiva)."""
        for name in list(self._lru):
            self.runtime.unload(name)
        self._lru.clear()


class MlxModelRuntime:
    """Adapter reale per Apple Silicon via `mlx-lm` (Gemma 4 12B da HuggingFace).

    Non usa Ollama: carica direttamente i pesi da HF con mlx-lm. Lo scaricamento avviene
    rilasciando i riferimenti Python e forzando il gc: su Apple Silicon la memoria
    unificata viene liberata non appena gli oggetti mlx non sono piu' raggiungibili.

    Import lazy di `mlx_lm`: il modulo si importa anche su ambienti senza MLX, cosi' i
    test di invarianti (che usano FakeModelRuntime) non richiedono il download dei pesi.
    L'adapter reale viene validato allo spike del Gate P1 (Microtask 3.2).
    """

    def __init__(self, default_num_ctx: int = 16000) -> None:
        self.default_num_ctx = default_num_ctx
        # name -> (model, tokenizer, num_ctx)
        self._models: dict[str, tuple[object, object, int]] = {}

    def _load_mlx(self):
        import mlx_lm  # lazy: pesante, solo quando si serve davvero un modello

        return mlx_lm

    def load(self, name: str, num_ctx: int) -> None:
        if name in self._models:
            logger.info("mlx: %s gia' residente, skip", name)
            return
        mlx_lm = self._load_mlx()
        logger.info("mlx: caricamento %s (num_ctx=%d)", name, num_ctx)
        model, tokenizer = mlx_lm.load(name)
        self._models[name] = (model, tokenizer, num_ctx)

    def unload(self, name: str) -> None:
        entry = self._models.pop(name, None)
        if entry is None:
            return
        # Rilascia i riferimenti e forza il gc: libera la memoria unificata Apple.
        del entry
        gc.collect()
        logger.info("mlx: scaricato %s (memoria rilasciata)", name)

    def resident(self) -> set[str]:
        return set(self._models)

    def generate(self, name: str, prompt: str = "", messages: list[dict] | None = None, **kwargs) -> str:
        """Genera testo con un modello residente (fail-closed se non caricato)."""
        if name not in self._models:
            raise RuntimeError(
                f"modello {name!r} non residente: chiamare use() via il Multiplexer prima"
            )
        mlx_lm = self._load_mlx()
        import mlx.core as mx
        
        model, tokenizer, num_ctx = self._models[name]
        
        # Estrai opzioni Port -> mappa ai veri parametri mlx_lm
        max_tokens = kwargs.pop("max_tokens", 512)
        temp = kwargs.pop("temp", None)
        seed = kwargs.pop("seed", None)
        
        # Mappa opzioni MLX
        generate_kwargs = {"max_tokens": max_tokens}
        
        if seed is not None:
            mx.random.seed(seed)
        
        if temp is not None:
            # Crea un sampler. temp > 0 o 0.0 gestito dal make_sampler di mlx_lm
            sampler = mlx_lm.sample_utils.make_sampler(temp=temp)
            generate_kwargs["sampler"] = sampler
            
        # Altre kwargs rimanenti (es. per future estensioni) le accodiamo
        generate_kwargs.update(kwargs)
        
        if messages:
            prompt = tokenizer.apply_chat_template(
                messages, 
                tokenize=False, 
                add_generation_prompt=True,
                enable_thinking=True
            )
            
        response = mlx_lm.generate(model, tokenizer, prompt=prompt, **generate_kwargs)
        
        # Parsing del canale di ragionamento (es. Gemma 4)
        # Formato atteso: <|channel>thought\n...<channel|>Risposta finale
        if "<|channel>thought" in response:
            parts = response.split("<channel|>", 1)
            if len(parts) < 2:
                raise RuntimeError("Formato reasoning incompleto: impossibile estrarre la risposta finale")
            response = parts[1].lstrip("\n")
            
        return response
