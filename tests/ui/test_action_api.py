import pytest
from pydantic import ValidationError
import chainlit as cl

def test_chainlit_action_api_anti_drift():
    """
    Test anti-deriva: verifica che l'API cl.Action accetti i parametri attesi.
    Nella versione 2.11 è richiesto 'payload' (non 'value').
    Costruiamo l'azione reale, se in futuro l'API cambia questo test fallirà.
    """
    try:
        action = cl.Action(
            name="feedback",
            payload={"verdetto": "conferma"},
            label="✓ Conferma"
        )
        assert action.name == "feedback"
        assert action.payload == {"verdetto": "conferma"}
        assert action.label == "✓ Conferma"
    except ValidationError as e:
        pytest.fail(f"API cl.Action cambiata o non valida: {e}")
