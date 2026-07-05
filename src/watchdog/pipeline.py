from __future__ import annotations
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

def filename_to_stem(filename: str) -> str:
    """Stem deterministico: lowercase, no estensione, spazi→underscore."""
    return Path(filename).stem.lower().replace(" ", "_")

def ingest_file(filepath: str, role: str, conn, is_update: bool = False) -> dict:
    """Orchestra l'ingestion reale di UN file rilevato dal Watchdog (B4).

    Non piu' un segnaposto: risolve il profilo brand dal filename (per l'avviso
    'default passthrough') e delega tutta la pipeline a
    `src.ingestion.ingest_one.ingest_document` (estrazione Docling -> chunking ->
    embedding -> upsert Qdrant -> registro versioni/ruoli Postgres). La pulizia
    dei chunk vecchi (anti-orfani su re-ingest) e' gestita DENTRO ingest_document
    (delete_document_points prima dell'upsert, B3).

    Ritorna il riepilogo di ingest_document arricchito con `is_default_profile`
    (usato dal worker per scrivere `profile_warning` in tracked_files).
    """
    # import locali: mantengono leggero l'import del modulo watchdog e i test
    # possono monkeypatchare ingest_document/resolve_brand_profile facilmente.
    from src.ingestion.ingest_one import ingest_document
    from src.watchdog.profile import resolve_brand_profile

    filename = Path(filepath).name
    doc_stem = filename_to_stem(filename)
    profile_name, is_default = resolve_brand_profile(filename, conn)
    logger.info(
        "Ingesting %s (is_update=%s) role=%s profilo=%s%s",
        filepath, is_update, role, profile_name,
        " [DEFAULT passthrough: codici NON normalizzati]" if is_default else "")

    result = ingest_document(filepath, doc_stem=doc_stem, role=role, conn=conn,
                             is_update=is_update)
    result["is_default_profile"] = is_default
    logger.info("Ingerito %s: %d chunk -> %d punti (profilo=%s)",
                doc_stem, result["n_chunks"], result["n_points"], profile_name)
    return result
