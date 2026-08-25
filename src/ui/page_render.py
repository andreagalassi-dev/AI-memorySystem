import logging
from pathlib import Path

import fitz

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
RENDERS_DIR = ROOT_DIR / "output" / "visual" / "renders"
CORPUS_DIR = ROOT_DIR / "corpus"

RENDER_DPI = 150
MAX_SIDE_PX = 1540

# Un doc_stem non e' un percorso e non e' un pattern: e' l'identificatore di un
# documento (`public.documents.slug`). Vietati i separatori di percorso e i
# METACARATTERI GLOB, perche' `resolve_pdf` interpola il valore dentro un
# pattern `glob()`: verificato il 25/08 sul corpus vivo, `doc_stem="Mamoli*"`
# risolveva `corpus/original_dataset/Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf` e
# `doc_stem="*"` il primo PDF trovato. Un pattern non compare mai in
# `documents.slug` (147 slug su 147 conformi a questa regola, misurato), quindi
# la guardia di ruolo lo negherebbe comunque: questo controllo esiste perche'
# l'input NON deve arrivare al filesystem in forma di pattern, chiunque lo
# chiami e per qualunque motivo.
_CARATTERI_VIETATI = ("/", "\\", "*", "?", "[", "]")
MAX_DOC_STEM_LEN = 200


def is_safe_doc_stem(doc_stem: object) -> bool:
    """Il valore e' un identificatore di documento plausibile? (non: esistente)

    Fail-closed sulla FORMA. La visibilita' per ruolo e' un'altra domanda e ha
    un'altra casa: `src/registry/access.py::doc_visible_to`.
    """
    if not isinstance(doc_stem, str):
        return False
    if not doc_stem or doc_stem != doc_stem.strip():
        return False
    if len(doc_stem) > MAX_DOC_STEM_LEN:
        return False
    if any(c in doc_stem for c in _CARATTERI_VIETATI):
        return False
    if doc_stem.startswith(".") or ".." in doc_stem:
        return False
    return all(ord(c) >= 32 for c in doc_stem)


def resolve_pdf(doc_stem: str) -> Path | None:
    """Risolve il percorso del PDF sorgente nel corpus."""
    if not is_safe_doc_stem(doc_stem):
        logger.warning("resolve_pdf: doc_stem non plausibile, ignorato: %r", doc_stem)
        return None
    return next(CORPUS_DIR.glob(f"**/{doc_stem}.pdf"), None)

def ensure_page_render(doc_stem: str, page: int) -> Path | None:
    """
    Ritorna il path del PNG della pagina citata (1-based).
    (a) Riusa il render esistente in output/visual/renders/
    (b) Altrimenti lo genera on-demand dal PDF in corpus/ (se esiste).
    Ritorna None in caso di errori o file mancanti, senza lanciare eccezioni.
    """
    # Prima di comporre QUALUNQUE percorso: `RENDERS_DIR / doc_stem` con un
    # doc_stem tipo `../../x` uscirebbe dalla cartella dei render.
    if not is_safe_doc_stem(doc_stem) or not isinstance(page, int) or isinstance(page, bool):
        return None
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
