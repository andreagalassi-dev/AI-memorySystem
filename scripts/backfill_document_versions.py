import sys
from pathlib import Path
import psycopg
from psycopg.sql import SQL, Identifier

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.registry.registry import register_document
from src.common.roles import parse_role

def backfill_missing_documents(conn: psycopg.Connection, schema_estrazione: str = "estrazione") -> tuple[int, int]:
    """Backfills missing document_versions for doc_stems present in estrazione.righe_listino.
    
    Returns:
        (n_registered, n_skipped)
    """
    n_registered = 0
    n_skipped = 0
    
    with conn.cursor() as cur:
        query = SQL("SELECT DISTINCT doc_stem FROM {}.righe_listino").format(Identifier(schema_estrazione))
        cur.execute(query)
        stems = [row[0] for row in cur.fetchall() if row[0]]
        
    for stem in stems:
        # register_document is idempotent. It returns created=True if it was just added
        res = register_document(
            conn,
            slug=stem,
            category=None,
            roles=[parse_role("tecnico")],
            doc_stem=stem,
            langs=[],
            version_no=1
        )
        if res.created:
            n_registered += 1
        else:
            n_skipped += 1
            
    return n_registered, n_skipped

def main():
    from src.backend.clients.postgres_client import conninfo
    
    with psycopg.connect(conninfo()) as conn:
        print("Inizio backfill document_versions...")
        n_reg, n_skip = backfill_missing_documents(conn)
        conn.commit()
        print(f"Backfill completato. Registrati: {n_reg}, Saltati (già presenti): {n_skip}")

if __name__ == "__main__":
    main()
