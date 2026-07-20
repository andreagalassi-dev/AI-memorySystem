from __future__ import annotations

from src.ingestion.extraction.schema import RigaListino


def is_spillover_row(descrizione: str | None) -> bool:
    """Valuta se la descrizione di una riga è probabilmente uno 'spillover' (dati di più colonne fusi).
    
    Criteri (validati empiricamente):
    - token = descrizione.split()
    - value_token = token che contiene almeno una cifra
    - max_rep = massima molteplicità di un token identico
    - SPILLOVER se: len(value_tokens) >= 4 OPPURE max_rep >= 3
    """
    if not descrizione:
        return False
        
    tokens = descrizione.split()
    if not tokens:
        return False
        
    value_tokens = [t for t in tokens if any(c.isdigit() for c in t)]
    max_rep = max(tokens.count(t) for t in set(tokens))
    
    return len(value_tokens) >= 4 or max_rep >= 3


def is_jumbled_page(righe: list[RigaListino], soglia: float = 0.40) -> bool:
    """Valuta se un'intera pagina è jumbled (dominata da righe spillover).
    
    Criteri:
    - (righe_spillover / righe_totali) > soglia
    - Applicato SOLO se righe_totali >= 4
    """
    if len(righe) < 4:
        return False
        
    spillover_count = sum(1 for r in righe if is_spillover_row(r.descrizione))
    return (spillover_count / len(righe)) > soglia
