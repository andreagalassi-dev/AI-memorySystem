import pytest
from pathlib import Path
from scripts.audit_index_coverage import classify_pdf

def test_classify_pdf_indexed():
    doc_slugs = {"test_doc"}
    doc_map = []
    assert classify_pdf(Path("corpus/test_doc.pdf"), doc_slugs, doc_map) == "INDICIZZATO"

def test_classify_pdf_indexed_by_name():
    doc_slugs = {"test_doc.pdf"}
    doc_map = []
    assert classify_pdf(Path("corpus/test_doc.pdf"), doc_slugs, doc_map) == "INDICIZZATO"

def test_classify_pdf_ambiguous():
    doc_slugs = {"test_doc", "test_doc.pdf"}
    doc_map = []
    assert classify_pdf(Path("corpus/test_doc.pdf"), doc_slugs, doc_map) == "DA_INDAGARE"

def test_classify_pdf_mai_tentato():
    doc_slugs = {"other_doc"}
    doc_map = ["Mamoli_*"]
    assert classify_pdf(Path("corpus/unknown.pdf"), doc_slugs, doc_map) == "MAI_TENTATO"

def test_classify_pdf_formato_non_gestito(monkeypatch):
    doc_slugs = set()
    doc_map = ["BadFile_*"]
    
    def mock_dispatch(*args, **kwargs):
        from src.ingestion.fast_dispatch import CorruptPdfError
        raise CorruptPdfError("bad")
        
    monkeypatch.setattr("src.ingestion.fast_dispatch.dispatch_pdf", mock_dispatch)
    assert classify_pdf(Path("corpus/BadFile_1.pdf"), doc_slugs, doc_map) == "FORMATO_NON_GESTITO"

def test_classify_pdf_scartato_dispatch(monkeypatch):
    doc_slugs = set()
    doc_map = ["Encrypted_*"]
    
    def mock_dispatch(*args, **kwargs):
        from src.ingestion.fast_dispatch import EncryptedPdfError
        raise EncryptedPdfError("encrypted")
        
    monkeypatch.setattr("src.ingestion.fast_dispatch.dispatch_pdf", mock_dispatch)
    assert classify_pdf(Path("corpus/Encrypted_1.pdf"), doc_slugs, doc_map) == "SCARTATO_DISPATCH"

def test_classify_pdf_fallito_estrazione(monkeypatch):
    doc_slugs = set()
    doc_map = ["Valid_*"]
    
    def mock_dispatch(*args, **kwargs):
        return True # success
        
    monkeypatch.setattr("src.ingestion.fast_dispatch.dispatch_pdf", mock_dispatch)
    assert classify_pdf(Path("corpus/Valid_1.pdf"), doc_slugs, doc_map) == "FALLITO_ESTRAZIONE"

def test_classify_pdf_non_pdf():
    doc_slugs = set()
    doc_map = ["*"]
    assert classify_pdf(Path("corpus/image.jpg"), doc_slugs, doc_map) == "FORMATO_NON_GESTITO"
