import os
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import fitz

from src.ui.page_render import ensure_page_render, resolve_pdf, RENDERS_DIR

def test_resolve_pdf_not_found():
    assert resolve_pdf("non_existent_doc_123") is None

def test_ensure_page_render_missing_pdf():
    # Se il PDF non c'è e non c'è il render, ritorna None
    with patch("src.ui.page_render.resolve_pdf", return_value=None):
        res = ensure_page_render("missing_pdf", 1)
        assert res is None

def test_ensure_page_render_existing_render(tmp_path):
    # Se il render esiste già, ritorna il path
    doc_stem = "existing_render_doc"
    page = 2
    
    with patch("src.ui.page_render.RENDERS_DIR", tmp_path):
        doc_dir = tmp_path / doc_stem
        doc_dir.mkdir(parents=True)
        png_path = doc_dir / f"p{page:04d}.png"
        png_path.write_text("dummy image data")
        
        # Chiamata alla funzione
        res = ensure_page_render(doc_stem, page)
        
        assert res == png_path

def test_ensure_page_render_on_demand(tmp_path):
    # Creiamo un piccolo PDF fasullo per testare il rendering
    doc_stem = "test_on_demand"
    pdf_path = tmp_path / f"{doc_stem}.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(50, 50), "Test PDF")
    doc.save(pdf_path)
    doc.close()

    with patch("src.ui.page_render.RENDERS_DIR", tmp_path / "renders"), \
         patch("src.ui.page_render.resolve_pdf", return_value=pdf_path):
        
        # Test pagina valida
        res = ensure_page_render(doc_stem, 1)
        assert res is not None
        assert res.exists()
        assert res.name == "p0001.png"
        
        # Test pagina fuori range
        res_invalid = ensure_page_render(doc_stem, 2)
        assert res_invalid is None
