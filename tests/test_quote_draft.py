from unittest.mock import patch
from src.agent.quote_draft import generate_quote_draft
from src.common.roles import Role
from src.graph.pipeline import Evidence

@patch("src.agent.quote_draft.answer")
def test_generate_quote_draft(mock_answer):
    ev_alta = Evidence(
        doc="doc1", page=1, lang="it", code="CODE1", desc="Pistone",
        text=None, qty=None, price=None, type="text", status=None, warnings=[], score=1.0
    )
    
    ev_media = Evidence(
        doc="doc2", page=1, lang="it", code="CODE2", desc="Filtro",
        text=None, qty=None, price=None, type="text", status=None, warnings=[], score=0.8
    )
    
    def side_effect(query, role):
        if query == "req_alta":
            return {"final_level": "ALTA", "evidence": [ev_alta]}
        elif query == "req_media":
            return {"final_level": "MEDIA", "evidence": [ev_media]}
        else:
            return {"final_level": "BASSA", "evidence": []}
            
    mock_answer.side_effect = side_effect
    
    requests = ["req_alta", "req_media", "req_bassa"]
    res = generate_quote_draft(requests, Role.TECNICO)
    
    assert len(res) == 3
    
    assert res[0]["status"] == "OK"
    assert res[0]["codice_proposto"] == "CODE1"
    assert res[0]["confidenza"] == "ALTA"
    
    assert res[1]["status"] == "DA VERIFICARE"
    assert res[1]["codice_proposto"] == "CODE2"
    assert res[1]["confidenza"] == "MEDIA"
    
    assert res[2]["status"] == "DA VERIFICARE"
    assert res[2]["codice_proposto"] == ""
    assert res[2]["confidenza"] == "BASSA"
