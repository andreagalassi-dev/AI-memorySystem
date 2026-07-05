"""D1 - Estrazione Distinte Base (BOM)."""

from __future__ import annotations

import logging
from qdrant_client import QdrantClient, models

from src.common.roles import Role
from src.retrieval.search import apply_hop2, access_filter
from src.ingestion.embedding import COLLECTION

logger = logging.getLogger(__name__)

def get_bom(client: QdrantClient, kit_code: str, role: Role) -> list[dict]:
    """Recupera la distinta base (BOM) di un assembly.
    
    1. Trova l'assembly hit in Qdrant tramite ricerca esatta per codice e permessi.
    2. Usa `apply_hop2` per recuperare i figli (che propaga il check sui permessi).
    3. Estrae in modo deterministico codice, descrizione, qta, pagina e fonte.
    """
    # 1. Costruiamo il filtro: codice esatto, è un assembly, permessi e versione corrente.
    base_filter = access_filter("current", role)
    
    q_filter = models.Filter(
        must=[
            models.FieldCondition(key="code", match=models.MatchValue(value=kit_code)),
            models.FieldCondition(key="is_assembly", match=models.MatchValue(value=True)),
        ] + base_filter.must
    )
    
    # 2. Cerchiamo il nodo padre in modo esatto (non semantico)
    records, _ = client.scroll(COLLECTION, scroll_filter=q_filter, limit=1)
    
    if not records:
        logger.warning("Nessun assembly trovato per il codice %s con il ruolo %s", kit_code, role.value)
        return []
        
    parent_record = records[0]
    
    # Convertiamo in ScoredPoint per la compatibilità con apply_hop2
    parent_hit = models.ScoredPoint(
        id=parent_record.id,
        version=getattr(parent_record, "version", 0),
        score=1.0,
        payload=parent_record.payload,
        vector=None
    )
    
    # 3. Invoca Hop 2
    _, children_hits, hop2_warning = apply_hop2(client, [parent_hit], role)
    
    if hop2_warning:
        logger.warning("Hop 2 warning: %s", hop2_warning)
        
    bom_items = []
    for hit in children_hits:
        p = getattr(hit, "payload", {}) or {}
        code = p.get("code")
        
        # Guardrail: se il chunk non ha un codice ricambio estratto, non e' un elemento valido per la BOM
        if not code:
            continue
            
        bom_items.append({
            "code": code,
            "description": p.get("description", ""),
            "quantity": p.get("quantity", ""),
            "page": p.get("page", ""),
            "source": p.get("doc_stem", "")
        })
        
    return bom_items
