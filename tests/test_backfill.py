import pytest
from psycopg.sql import SQL, Identifier

from scripts.backfill_document_versions import backfill_missing_documents
from src.registry.registry import get_version_by_stem, register_document
from src.common.roles import parse_role
from src.ingestion.extraction.load import init_schema as init_schema_estrazione
from src.ingestion.extraction.schema import RigaListino
from src.ingestion.extraction.load import upsert_righe

# Fixture condivisa dal conftest che inizializza lo schema test_registry (registry è iniettato e usa search_path)
# Ma `estrazione.righe_listino` ha bisogno dello schema estrazione (mockato o temporaneo).
# Creiamo uno schema estrazione temporaneo.

@pytest.fixture
def conn_with_estrazione(conn):
    test_schema = "test_estrazione_backfill"
    conn.execute(SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(Identifier(test_schema)))
    conn.execute(SQL("CREATE SCHEMA {}").format(Identifier(test_schema)))
    
    # Inizializziamo lo schema di estrazione
    init_schema_estrazione(conn, schema=test_schema)
    
    # Inizializziamo lo schema registry (che test_registry potrebbe aver già fatto, ma verifichiamo)
    from src.registry.registry import init_schema as init_schema_registry
    init_schema_registry(conn) # Usa il default o il search_path corrente
    # Wait, conn uses test_registry as search_path automatically in conftest.
    
    yield conn, test_schema

    conn.execute(SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(Identifier(test_schema)))


def test_backfill_missing_documents(conn_with_estrazione):
    conn, schema_estrazione = conn_with_estrazione
    
    # Registriamo già un documento per simulare uno pre-esistente
    register_document(conn, slug="doc_esistente", category=None, roles=[parse_role("tecnico")], doc_stem="doc_esistente", langs=[])
    
    # Inseriamo in righe_listino: uno esistente, e due NON esistenti
    righe = [
        RigaListino(doc_stem="doc_esistente", page=1, codice_normalizzato="C1", codice_originale="C1", descrizione="D1", prezzo=None),
        RigaListino(doc_stem="doc_nuovo_1", page=1, codice_normalizzato="C2", codice_originale="C2", descrizione="D2", prezzo=None),
        RigaListino(doc_stem="doc_nuovo_2", page=1, codice_normalizzato="C3", codice_originale="C3", descrizione="D3", prezzo=None)
    ]
    upsert_righe(conn, righe, schema=schema_estrazione)
    
    # Verify pre-condition: only doc_esistente is in registry
    assert get_version_by_stem(conn, "doc_esistente") is not None
    assert get_version_by_stem(conn, "doc_nuovo_1") is None
    assert get_version_by_stem(conn, "doc_nuovo_2") is None
    
    # Eseguiamo il backfill
    n_reg, n_skip = backfill_missing_documents(conn, schema_estrazione=schema_estrazione)
    assert n_reg == 2
    assert n_skip == 1
    
    # Verify post-condition
    assert get_version_by_stem(conn, "doc_esistente") is not None
    assert get_version_by_stem(conn, "doc_nuovo_1") is not None
    assert get_version_by_stem(conn, "doc_nuovo_2") is not None
    
    # Test idempotenza
    n_reg2, n_skip2 = backfill_missing_documents(conn, schema_estrazione=schema_estrazione)
    assert n_reg2 == 0
    assert n_skip2 == 3


def test_ingest_full_registers_document(conn):
    # Dobbiamo testare che `build_branches` chiami register_document
    from src.ingestion.ingest_full import build_branches
    from src.registry.registry import init_schema as init_schema_registry
    init_schema_registry(conn)

    assert get_version_by_stem(conn, "stem_test_ingest") is None

    # Call build_branches
    build_branches(
        pdf_path="/fake/path.pdf",
        doc_stem="stem_test_ingest",
        role="tecnico",
        conn=conn,
        multiplexer=None,
        model_name="fake",
        extractor=None,
        embedder=None,
        qdrant_client=None
    )

    # Document should be registered
    vers = get_version_by_stem(conn, "stem_test_ingest")
    assert vers is not None

    # Idempotenza: una seconda chiamata non deve fallire
    build_branches(
        pdf_path="/fake/path.pdf",
        doc_stem="stem_test_ingest",
        role="tecnico",
        conn=conn,
        multiplexer=None,
        model_name="fake",
        extractor=None,
        embedder=None,
        qdrant_client=None
    )
