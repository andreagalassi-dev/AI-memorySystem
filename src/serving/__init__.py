"""Serving layer: Model Multiplexer e adapter di runtime (Fase 1, BLOCCO 1).

Il runtime di inferenza e' un Port (Protocol `ModelRuntime`); gli adapter concreti
(FakeModelRuntime per i test, MlxModelRuntime per Apple Silicon) sono i plug.
`ModelMultiplexer` e' il Watchdog che garantisce le invarianti di memoria.
"""

from src.serving.multiplexer import (
    FakeModelRuntime,
    MlxModelRuntime,
    ModelMultiplexer,
    ModelRuntime,
)

__all__ = [
    "FakeModelRuntime",
    "MlxModelRuntime",
    "ModelMultiplexer",
    "ModelRuntime",
]
