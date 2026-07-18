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


def test_mlx_runtime_parameter_translation(monkeypatch):
    """MlxModelRuntime deve tradurre temp e seed in sampler e mx.random.seed per mlx-lm 0.31+."""
    import sys
    from unittest.mock import MagicMock
    from src.serving.multiplexer import MlxModelRuntime
    
    # Mock moduli mancanti (mlx e mlx_lm)
    mock_mlx = MagicMock()
    mock_mlx_lm = MagicMock()
    mock_sampler = MagicMock()
    mock_mlx_lm.sample_utils.make_sampler.return_value = mock_sampler
    
    monkeypatch.setitem(sys.modules, "mlx", mock_mlx)
    monkeypatch.setitem(sys.modules, "mlx.core", mock_mlx.core)
    monkeypatch.setitem(sys.modules, "mlx_lm", mock_mlx_lm)
    
    runtime = MlxModelRuntime()
    # Iniettiamo a forza un modello residente
    mock_model = MagicMock()
    mock_tokenizer = MagicMock()
    mock_tokenizer.apply_chat_template.return_value = "prompt formattato"
    runtime._models["test-model"] = (mock_model, mock_tokenizer, 16000)
    
    # Eseguiamo generate con opzioni Port
    runtime.generate("test-model", messages=[{"role": "user", "content": "ciao"}], temp=0.5, seed=42, max_tokens=100)
    
    # Verifichiamo la traduzione
    mock_mlx.core.random.seed.assert_called_once_with(42)
    mock_mlx_lm.sample_utils.make_sampler.assert_called_once_with(temp=0.5)
    
    # Verifichiamo che generate abbia ricevuto sampler e max_tokens
    mock_mlx_lm.generate.assert_called_once()
    kwargs = mock_mlx_lm.generate.call_args.kwargs
    assert kwargs["prompt"] == "prompt formattato"
    assert kwargs["max_tokens"] == 100
    assert kwargs["sampler"] is mock_sampler
    assert "temp" not in kwargs
    assert "seed" not in kwargs
