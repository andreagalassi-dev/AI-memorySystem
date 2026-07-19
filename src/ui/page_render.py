import logging
from pathlib import Path

import fitz

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
RENDERS_DIR = ROOT_DIR / "output" / "visual" / "renders"
CORPUS_DIR = ROOT_DIR / "corpus"

RENDER_DPI = 150
MAX_SIDE_PX = 1540

def resolve_pdf(doc_stem: str) -> Path | None:
    """Risolve il percorso del PDF sorgente nel corpus."""
    return next(CORPUS_DIR.glob(f"**/{doc_stem}.pdf"), None)

def ensure_page_render(doc_stem: str, page: int) -> Path | None:
    """
    Ritorna il path del PNG della pagina citata (1-based).
    (a) Riusa il render esistente in output/visual/renders/
    (b) Altrimenti lo genera on-demand dal PDF in corpus/ (se esiste).
    Ritorna None in caso di errori o file mancanti, senza lanciare eccezioni.
    """
    png = RENDERS_DIR / doc_stem / f"p{page:04d}.png"
    if png.exists():
        return png
    
    pdf = resolve_pdf(doc_stem)
    if pdf is None:
        return None
    
    try:
        with fitz.open(pdf) as fdoc:
            if page < 1 or page > fdoc.page_count:
                return None
            fpage = fdoc[page - 1]
            rect = fpage.rect
            scale = min(RENDER_DPI / 72, MAX_SIDE_PX / max(rect.width, rect.height))
            pix = fpage.get_pixmap(matrix=fitz.Matrix(scale, scale))
            
            # Additivo, mai sovrascrivere o eliminare
            png.parent.mkdir(parents=True, exist_ok=True)
            if not png.exists():
                pix.save(png)
        return png
    except Exception as e:
        logger.warning(f"Errore nel rendering di {doc_stem} p.{page}: {e}")
        return None
