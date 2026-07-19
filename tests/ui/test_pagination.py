import pytest
from src.ui.pagination import calculate_next_page

def test_calculate_next_page():
    assert calculate_next_page(5, "succ") == 6
    assert calculate_next_page(5, "prec") == 4
    
    # Non permette pagine <= 0 se controllato altrove, ma la funzione base fa solo +- 1. 
    # Le regole di business dicono che page_render ritorna None se fuori range, quindi calcoliamo liberi.
    assert calculate_next_page(1, "prec") == 0
    
    with pytest.raises(ValueError, match="Invalid direction"):
        calculate_next_page(5, "unknown")
