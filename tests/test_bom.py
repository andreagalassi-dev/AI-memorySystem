"""Test per l'estrazione della Distinta Base (BOM)."""

from unittest.mock import MagicMock, patch
from qdrant_client import models

from src.retrieval.bom import get_bom
from src.common.roles import Role

@patch("src.retrieval.bom.apply_hop2")
def test_get_bom_success(mock_apply_hop2):
    """Testa l'estrazione di una BOM valida da un assembly."""
    client = MagicMock()
    
    # Mock del parent (nodo assembly)
    parent_record = models.Record(
        id=1,
        version=1,
        payload={"code": "KIT-001", "is_assembly": True}
    )
    
    # Simula la risposta di client.scroll per trovare l'assembly
    client.scroll.return_value = ([parent_record], None)
    
    # Mock della risposta di apply_hop2
    # Ritorna: (grouped_hits, children_hits, hop2_warning)
    mock_children = [
        models.ScoredPoint(
            id=2, version=1, score=1.0, vector=None,
            payload={"code": "CHILD-001", "description": "Desc 1", "quantity": "2", "page": 5, "doc_stem": "doc1"}
        ),
        models.ScoredPoint(
            id=3, version=1, score=1.0, vector=None,
            payload={"code": "CHILD-002", "description": "Desc 2", "quantity": "1"}
        ),
        models.ScoredPoint(
            id=4, version=1, score=1.0, vector=None,
            payload={"description": "No code"} # Guardrail: no code -> skipped
        )
    ]
    mock_apply_hop2.return_value = ([], mock_children, None)
    
    bom = get_bom(client, "KIT-001", Role.TECNICO)
    
    assert len(bom) == 2
    assert bom[0]["code"] == "CHILD-001"
    assert bom[0]["description"] == "Desc 1"
    assert bom[0]["quantity"] == "2"
    assert bom[0]["page"] == 5
    assert bom[0]["source"] == "doc1"
    
    assert bom[1]["code"] == "CHILD-002"
    assert bom[1]["description"] == "Desc 2"
    assert bom[1]["quantity"] == "1"
    assert bom[1]["page"] == ""
    assert bom[1]["source"] == ""
    
    # Verifica che apply_hop2 sia stato chiamato
    mock_apply_hop2.assert_called_once()
    args, _ = mock_apply_hop2.call_args
    assert args[0] == client
    assert args[1][0].payload["code"] == "KIT-001"
    assert args[2] == Role.TECNICO

def test_get_bom_not_found():
    """Testa il caso in cui l'assembly non esista."""
    client = MagicMock()
    # Ritorna lista vuota da scroll (niente trovato)
    client.scroll.return_value = ([], None)
    
    bom = get_bom(client, "MISSING", Role.TECNICO)
    assert bom == []
