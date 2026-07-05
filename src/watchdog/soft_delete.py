from __future__ import annotations
import logging
from src.registry.registry import get_version_by_stem, backfill_payload, export_backfill
from src.backend.clients.qdrant_client import get_qdrant_client

logger = logging.getLogger(__name__)

def apply_soft_delete(conn, document_id: int):
    """
    Applica il soft delete archiviante (P7 §3.1).
    1. Aggiorna document_versions a 'deleted'
    2. Propaga a Qdrant
    3. tracked_files viene aggiornato separatamente dal worker
    """
    # 1. Update document_versions
    # Ottieni la current version per trovare lo stem da backfillare
    row = conn.execute(
        "SELECT doc_stem FROM document_versions "
        "WHERE document_id = %s AND version_status = 'current'",
        (document_id,)
    ).fetchone()
    
    if not row:
        logger.warning(f"Soft delete fallito: nessuna versione 'current' per document_id={document_id}")
        return
        
    doc_stem = row[0]
    
    conn.execute(
        "UPDATE document_versions SET version_status = 'deleted' "
        "WHERE document_id = %s AND version_status = 'current'",
        (document_id,)
    )
    conn.commit()
    
    # 2. Backfill Qdrant per aggiornare il payload
    try:
        from src.registry.sync import sync_collection
        # Estraiamo il nuovo stato (che ora sarà 'deleted')
        records = export_backfill(conn)
        if doc_stem in records:
            # Sync riapplica il payload corretto (con version_status='deleted')
            sync_collection(records)
            logger.info(f"Soft delete applicato con successo per stem={doc_stem}")
    except Exception as e:
        logger.error(f"Errore durante sync qdrant per soft delete: {e}")
