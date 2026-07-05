from __future__ import annotations
import os
import hashlib
import logging
from typing import NamedTuple

logger = logging.getLogger(__name__)

class FileState(NamedTuple):
    filename: str
    size: int
    mtime: float
    hash: str | None = None

def get_file_hash(filepath: str) -> str:
    hasher = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(4096 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()

def scan_directory(corpus_path: str) -> dict[str, FileState]:
    """Rileva tutti i file nella cartella, ritornando la map filename -> FileState senza hash"""
    current_files = {}
    if not os.path.exists(corpus_path):
        return current_files
        
    for root, _, files in os.walk(corpus_path):
        for file in files:
            if file.startswith('.'):
                continue
            filepath = os.path.join(root, file)
            # Normalizziamo il filename (mantenendo solo il nome del file stesso, non i path intermedi, oppure
            # usiamo un path relativo dal corpus_root. Assumiamo che il corpus sia piatto per semplicita')
            rel_path = os.path.relpath(filepath, corpus_path)
            try:
                stat = os.stat(filepath)
                current_files[rel_path] = FileState(
                    filename=rel_path,
                    size=stat.st_size,
                    mtime=stat.st_mtime
                )
            except OSError as e:
                logger.warning(f"Impossibile leggere file {filepath}: {e}")
                
    return current_files

def diff_files(conn, current_files: dict[str, FileState], corpus_path: str) -> dict[str, str]:
    """
    Ritorna dict: filename -> action ('new', 'modified', 'deleted', 'processing_orphan')
    Applica il pre-filtro Fase 1 (size/mtime) e se necessario Fase 2 (SHA-256).
    """
    actions = {}
    
    # 1. Carica i tracked_files esistenti
    tracked = {}
    rows = conn.execute(
        "SELECT filename, file_hash, file_size, file_mtime, status "
        "FROM tracked_files"
    ).fetchall()
    for row in rows:
        tracked[row[0]] = {
            "file_hash": row[1],
            "file_size": row[2],
            "file_mtime": row[3],
            "status": row[4]
        }
        
    # 2. Controllo file su disco
    for filename, state in current_files.items():
        t = tracked.get(filename)
        
        if t is None:
            actions[filename] = "new"
            continue
            
        if t["status"] == "processing":
            actions[filename] = "processing_orphan"
            continue
            
        if t["status"] == "failed":
            continue # Skip failed files
            
        # Fase 1: Economica
        # Convertiamo state.mtime (float epoch) in un modo confrontabile con timestamptz?
        # In python postgres row[3] è datetime. mtime è float. Un po' fragile. 
        # Alternativamente ci basiamo su size diverso -> update. Se size uguale ma mtime diverso -> check hash.
        # Per sicurezza, Fase 1 basata su: size diverso -> Fase 2. 
        size_changed = t["file_size"] is None or t["file_size"] != state.size
        # Anche senza mtime esatto, un size diverso è garanzia. Se size identico, magari mtime è lo stesso o no, 
        # ignoriamo mtime se non possiamo confrontarlo facilmente e ci affidiamo all'hash nel dubbio, 
        # oppure semplifichiamo: se size identico -> skip hash. No, un PDF modificato potrebbe avere stesso size (raro ma possibile).
        # Calcoliamo sempre hash se mtime non corrisponde. Al primo giro file_mtime potrebbe essere null.
        
        needs_hash = False
        if size_changed:
            needs_hash = True
        else:
            # mtime in python da os.stat è float, in db è datetime tz-aware. 
            if t["file_mtime"] is None:
                needs_hash = True
            else:
                db_mtime = t["file_mtime"].timestamp()
                # Tolleranza di 1 secondo per problemi di filesystem
                if abs(db_mtime - state.mtime) > 1.0:
                    needs_hash = True
                    
        if needs_hash:
            filepath = os.path.join(corpus_path, filename)
            new_hash = get_file_hash(filepath)
            if new_hash != t["file_hash"]:
                actions[filename] = "modified"
                
    # 3. Controllo file eliminati
    for filename, t in tracked.items():
        if filename not in current_files and t["status"] in ("active", "processing", "failed", "queued"):
            actions[filename] = "deleted"
            
    return actions
