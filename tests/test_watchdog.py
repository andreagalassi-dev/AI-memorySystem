from src.watchdog.scanner import scan_directory, diff_files, FileState
import pytest

class MockConn:
    def __init__(self, data=None):
        self.data = data or []
        self.log = []
    def execute(self, query, params=None):
        self.log.append((query, params))
        class Result:
            def __init__(self, rows):
                self.rows = rows
            def fetchall(self):
                return self.rows
            def fetchone(self):
                return self.rows[0] if self.rows else None
        return Result(self.data)

def test_poller_idempotency():
    """Test idempotenza Fase 1 e Fase 2."""
    db_state = [
        ("file1.pdf", "hash1", 100, 1000.0, "active"),
        ("file2.pdf", "hash2", 200, 2000.0, "active"),
    ]
    conn = MockConn(db_state)
    
    current_files = {
        "file1.pdf": FileState("file1.pdf", 100, 1000.0), # Identico
        "file2.pdf": FileState("file2.pdf", 205, 2000.0), # Size cambiato
        "file3.pdf": FileState("file3.pdf", 300, 3000.0), # Nuovo
    }
    
    # Per evitare calcolo hash reale, patchamo get_file_hash
    import src.watchdog.scanner as scanner
    original_get_hash = scanner.get_file_hash
    scanner.get_file_hash = lambda p: "hash_new"
    
    diffs = diff_files(conn, current_files, "/dummy")
    
    scanner.get_file_hash = original_get_hash
    
    assert "file1.pdf" not in diffs # Skippato (Fase 1)
    assert diffs.get("file2.pdf") == "modified" # Modificato (Fase 2)
    assert diffs.get("file3.pdf") == "new"      # Nuovo

def test_poller_soft_delete():
    db_state = [
        ("file1.pdf", "hash1", 100, 1000.0, "active"),
    ]
    conn = MockConn(db_state)
    current_files = {} # File rimosso dal disco
    
    diffs = diff_files(conn, current_files, "/dummy")
    assert diffs.get("file1.pdf") == "deleted"

def test_poller_crash_recovery():
    db_state = [
        ("file1.pdf", "hash1", 100, 1000.0, "processing"), # Crash!
    ]
    conn = MockConn(db_state)
    current_files = {
        "file1.pdf": FileState("file1.pdf", 100, 1000.0),
    }
    diffs = diff_files(conn, current_files, "/dummy")
    assert diffs.get("file1.pdf") == "processing_orphan"
