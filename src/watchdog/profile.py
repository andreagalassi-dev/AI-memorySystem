from __future__ import annotations
import logging
from fnmatch import fnmatch

logger = logging.getLogger(__name__)

def resolve_brand_profile(filename: str, conn) -> tuple[str, bool]:
    """
    Risolve il profilo brand dal filename tramite doc_map a DB (glob pattern).
    Ritorna (profile_name, is_default).
    
    Se usa 'default' (passthrough): WARNING obbligatorio.
    """
    from src.registry.ingestion_profiles import load_profiles
    
    config = load_profiles(conn)
    for glob, profile_name in config.get("doc_map", {}).items():
        if fnmatch(filename.lower(), glob.lower()):
            return profile_name, False
            
    logger.warning(
        "BRAND PROFILE: nessun profilo per '%s' → 'default' (passthrough). "
        "Codici ricambio NON normalizzati. Aggiornare config/ingestion_doc_map "
        "se il documento contiene tabelle ricambi.", filename
    )
    return "default", True
