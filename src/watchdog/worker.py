from __future__ import annotations
import logging
from src.watchdog.scanner import scan_directory, diff_files, get_file_hash
from src.watchdog.soft_delete import apply_soft_delete
from src.watchdog.pipeline import ingest_file, filename_to_stem
import os

logger = logging.getLogger(__name__)

def process_diffs(conn, diffs: dict[str, str], corpus_path: str, default_role: str):
    """
    Elabora i file sequenzialmente (SERIALIZZATO).
    Una GPU alla volta per evitare OOM.
    """
    for filename, action in diffs.items():
        filepath = os.path.join(corpus_path, filename)
        
        try:
            if action in ("new", "modified", "processing_orphan"):
                logger.info(f"Processing {action} file: {filename}")
                
                # Setup
                new_hash = get_file_hash(filepath)
                stat = os.stat(filepath)
                
                # Update status to processing
                # Se è new, lo inseriamo
                if action == "new":
                    conn.execute(
                        "INSERT INTO tracked_files (filename, file_hash, file_size, file_mtime, status, queued_at) "
                        "VALUES (%s, %s, %s, to_timestamp(%s), 'processing', now()) "
                        "ON CONFLICT (filename) DO UPDATE SET "
                        "file_hash=EXCLUDED.file_hash, file_size=EXCLUDED.file_size, "
                        "file_mtime=EXCLUDED.file_mtime, status='processing', queued_at=now()",
                        (filename, new_hash, stat.st_size, stat.st_mtime)
                    )
                else:
                    conn.execute(
                        "UPDATE tracked_files SET status='processing', file_hash=%s, file_size=%s, file_mtime=to_timestamp(%s) "
                        "WHERE filename=%s",
                        (new_hash, stat.st_size, stat.st_mtime, filename)
                    )
                conn.commit()
                
                # La pulizia dei chunk vecchi (anti-orfani su modified/orphan) e'
                # gestita DENTRO ingest_document -> delete_document_points prima
                # dell'upsert (B3): niente duplicati della versione precedente.

                # Ingest reale (B4)
                is_update = (action == "modified")
                result = ingest_file(filepath, role=default_role, conn=conn,
                                     is_update=is_update)

                # Mark as active + profile_warning (profilo 'default' passthrough)
                # + link al documento logico (serve alla cascata soft-delete B5)
                conn.execute(
                    "UPDATE tracked_files SET status='active', last_processed_at=now(), "
                    "profile_warning=%s, document_id=%s WHERE filename=%s",
                    (bool(result.get("is_default_profile")),
                     result.get("document_id"), filename)
                )
                conn.commit()
                
            elif action == "deleted":
                logger.info(f"Soft deleting file: {filename}")
                
                # Get document_id
                row = conn.execute(
                    "SELECT document_id FROM tracked_files WHERE filename=%s", (filename,)
                ).fetchone()
                
                if row and row[0]:
                    apply_soft_delete(conn, row[0])
                    
                conn.execute(
                    "UPDATE tracked_files SET status='deleted', deleted_at=now() "
                    "WHERE filename=%s",
                    (filename,)
                )
                conn.commit()
                
        except Exception as e:
            logger.error(f"Failed to process {filename} ({action}): {e}", exc_info=True)
            # Increment retry count and set to failed
            conn.execute(
                "UPDATE tracked_files SET status='failed', error_log=%s, retry_count=retry_count+1 "
                "WHERE filename=%s",
                (str(e), filename)
            )
            conn.commit()
