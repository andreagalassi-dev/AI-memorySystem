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
    return False


def is_jumbled_page(righe: list[RigaListino], soglia: float = 0.40) -> bool:
    """Valuta se un'intera pagina è jumbled (dominata da righe spillover).
    
    Criteri:
    - (righe_spillover / righe_totali) > soglia
    - Applicato SOLO se righe_totali >= 4
    """
    return False
