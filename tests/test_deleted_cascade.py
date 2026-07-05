from src.graph.pipeline import _after_confidence, GraphState

def test_deleted_cascade_routing():
    # Setup test per routing a 3 livelli
    
    # 1. current -> MEDIA (nessun fallback)
    state = GraphState(level="MEDIA")
    assert _after_confidence(state, True, True) == "fusion"
    
    # 2. current -> BASSA (ha fallback superseded, non ancora fatto)
    state = GraphState(level="BASSA")
    assert _after_confidence(state, True, True) == "fallback_superseded"
    
    # 3. superseded -> BASSA (ha fallback deleted, fallback_superseded fatto)
    state = GraphState(level="BASSA", fallback_superseded_done=True)
    assert _after_confidence(state, True, True) == "fallback_deleted"
    
    # 4. deleted -> BASSA (tutti i fallback fatti)
    state = GraphState(level="BASSA", fallback_superseded_done=True, fallback_deleted_done=True)
    assert _after_confidence(state, True, True) == "refuse"

def test_warning_deleted_reconcile():
    from src.graph.pipeline import reconcile
    state = GraphState(
        level="BASSA", 
        answer="Non lo so",
        deleted_warning=True
    )
    # Reconcile override
    out = reconcile(state)
    assert out["final_level"] == "BASSA"
    assert "ELIMINATO" in out["final_answer"]
