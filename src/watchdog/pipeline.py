from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def filename_to_stem(filename: str) -> str:
    """Stem deterministico: lowercase, no estensione, spazi→underscore."""
    return Path(filename).stem.lower().replace(" ", "_")

def ingest_file(filepath: str, role: str, is_update: bool = False):
    """
    Orchestra: Docling → chunking → vettori Qdrant.
    Questa è la trasposizione automatica di scripts/ingest_chunks.py
    Per la Fase 0 eseguiamo programmaticamente o lanciando i task
    """
    # L'implementazione reale chiamerebbe l'estrattore Docling,
    # poi chunk_tables, ecc.
    # Assumiamo di usare funzioni esistenti o subprocess per ora.
    logger.info(f"Ingesting {filepath} (is_update={is_update}) with role {role}")
    # TODO: integrare chiamata reale a Docling/Ingestion P2/P3.
