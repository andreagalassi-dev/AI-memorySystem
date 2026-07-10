"""Fase 1 (hardening) — Model Multiplexer: unload-before-load + cap context — RED.

L'hardware locale non tiene tutti i modelli residenti insieme (PIANO-IMPLEMENTAZIONE.md
riga 6: scaricare gli inattivi prima di caricare i nuovi; riga 26: context window
8-16K per contenere la KV cache ~2-4GB). Questo modulo garantisce l'invariante di
residenza e clampa il context window a un cap duro, per prevenire OOM nel run massivo.

Ports & Adapters: `ModelRuntime` e' il Port del runtime di serving (reale = Ollama con
keep_alive=0 per evictare + num_ctx per il cap); `FakeModelRuntime` gira in memoria.
"""

from __future__ import annotations

from src.serving.multiplexer import FakeModelRuntime, ModelMultiplexer


def test_caricare_secondo_modello_scarica_il_primo():
    """max_resident=1: usare B dopo A deve evictare A (mai due pesi residenti)."""
    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)

    mux.use("modello_A")
    mux.use("modello_B")

    assert rt.resident() == {"modello_B"}
    assert ("unload", "modello_A") in rt.calls


def test_context_window_clampato_al_cap():
    """Una richiesta oltre il cap viene clampata: la KV cache resta sotto controllo."""
    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)

    eff = mux.use("modello_A", num_ctx=32000)

    assert eff == 16000
    assert ("load", "modello_A", 16000) in rt.calls


def test_riuso_modello_residente_non_ricarica():
    """Usare due volte lo stesso modello non provoca churn (idempotente)."""
    rt = FakeModelRuntime()
    mux = ModelMultiplexer(rt, max_resident=1, ctx_cap=16000)

    mux.use("modello_A")
    mux.use("modello_A")

    assert [c for c in rt.calls if c[0] == "load"] == [("load", "modello_A", 16000)]
