import os
import pytest
from src.retrieval.qwen3vl import _model, embed_query

@pytest.mark.skipif(not os.environ.get("RUN_VISUAL"), reason="Run with RUN_VISUAL=1")
def test_qwen3vl_carica_realmente_senza_skip_silenzioso():
    """Test rumoroso: se RUN_VISUAL=1, il modello deve scaricare/caricare i pesi veri.
    Se fallisce, deve essere un vero errore."""
    try:
        model = _model()
        assert model is not None
        
        # Facciamo una prova di inferenza veloce (il trunc a 1024 lo fa embed_query)
        vec = embed_query("test")
        assert len(vec) == 1024
    except Exception as e:
        pytest.fail(f"Fallimento rumoroso nel caricamento/inferenza di Qwen3-VL: {e}")

