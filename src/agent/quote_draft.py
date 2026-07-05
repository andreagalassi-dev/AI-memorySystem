"""Fase D2 - Generazione bozza preventivo da lista testuale."""

from __future__ import annotations

from src.common.roles import Role
from src.graph.pipeline import answer

def generate_quote_draft(requests: list[str], role: Role) -> list[dict]:
    """Interroga il sistema su ciascuna richiesta e genera una bozza."""
    draft = []
    
    for req in requests:
        if not req.strip():
            continue
            
        state = answer(req, role)
        
        level = state.get("final_level", "BASSA")
        
        # Estrarre l'evidenza
        ev = state.get("evidence", [])
        
        if level == "ALTA" and ev:
            top_hit = ev[0]
            code = top_hit.code or ""
            desc = top_hit.desc or ""
            source = top_hit.doc or ""
        else:
            # Se la confidenza non e' ALTA, lo indichiamo come "DA VERIFICARE"
            # Ma possiamo comunque mostrare il codice proposto se c'è un top_hit
            top_hit = ev[0] if ev else None
            code = top_hit.code if top_hit and top_hit.code else ""
            desc = top_hit.desc if top_hit and top_hit.desc else ""
            source = top_hit.doc if top_hit and top_hit.doc else ""
            
        draft.append({
            "richiesta": req,
            "codice_proposto": code,
            "descrizione": desc,
            "confidenza": level,
            "fonte": source,
            "status": "OK" if level == "ALTA" else "DA VERIFICARE"
        })
        
    return draft
