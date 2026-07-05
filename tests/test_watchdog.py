from src.watchdog.scanner import scan_directory, diff_files, FileState
import uuid
import pytest
from qdrant_client import models

from tests.conftest import needs_pg, needs_qdrant

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


# --- B4: ciclo completo del daemon (integrazione, SERVE DB) -------------------

_MD_PAGE = {
    1: (
        "| Codice | Descrizione | Q.ta |\n"
        "| --- | --- | --- |\n"
        "| WD-001 | Pompa idraulica completa gruppo | 1 |\n"
        "| WD-002 | Filtro olio ricambio originale | 2 |\n"
    )
}


@needs_qdrant
@needs_pg
def test_daemon_cycle_ingerisce_e_attiva(conn, monkeypatch, tmp_path):
    """Un PDF nella cartella monitorata -> scan+process_diffs -> file 'active',
    profile_warning (profilo default), document_id collegato, punti in Qdrant."""
    from src.registry.registry import init_schema
    from src.registry.ingestion_profiles import init_schema as init_profiles
    from src.backend.clients.qdrant_client import get_qdrant_client
    from src.watchdog.worker import process_diffs
    from src.ingestion.ingest_one import ingest_document as real_ingest

    init_schema(conn)          # documents/versions/roles/tracked_files
    init_profiles(conn)        # tabelle profili (vuote -> doc_map vuoto -> default)

    qc = get_qdrant_client()
    coll = f"test_wd_{uuid.uuid4().hex[:8]}"

    # estrazione Docling iniettata + collezione dedicata (mai la reale aike_chunks)
    def stub(filepath, *, doc_stem, role, conn, is_update=False, **kw):
        return real_ingest(filepath, doc_stem=doc_stem, role=role, conn=conn,
                           is_update=is_update, collection=coll,
                           extract_fn=lambda fp: _MD_PAGE)
    monkeypatch.setattr("src.ingestion.ingest_one.ingest_document", stub)

    pdf = tmp_path / "acme_listino.pdf"
    pdf.write_bytes(b"%PDF-1.4 contenuto finto")  # estrazione iniettata

    current = scan_directory(str(tmp_path))
    diffs = diff_files(conn, current, str(tmp_path))
    assert diffs.get("acme_listino.pdf") == "new"

    try:
        process_diffs(conn, diffs, str(tmp_path), default_role="tecnico")

        row = conn.execute(
            "SELECT status, profile_warning, document_id, error_log "
            "FROM tracked_files WHERE filename=%s", ("acme_listino.pdf",)
        ).fetchone()
        assert row is not None, "file non tracciato"
        assert row[0] == "active", f"stato={row[0]} error_log={row[3]}"
        assert row[1] is True, "profilo default -> profile_warning atteso True"
        assert row[2] is not None, "document_id non collegato (serve alla cascata B5)"

        n = qc.count(coll, exact=True, count_filter=models.Filter(must=[
            models.FieldCondition(key="doc_stem",
                                  match=models.MatchValue(value="acme_listino"))])).count
        assert n > 0, "nessun punto ingerito in Qdrant"
    finally:
        if qc.collection_exists(coll):
            qc.delete_collection(coll)
