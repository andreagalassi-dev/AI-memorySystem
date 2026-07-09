"""Fase 1.1 — Tiling delle pagine per l'embedding visivo (PixelRAG).

L'attenzione dei VLM-embedder e' quadratica nei patch: scalare l'intera pagina sotto
il tetto di risoluzione distrugge i micro-dettagli degli esplosi. Si renderizza la
pagina ad alta risoluzione e la si spezza in una griglia di tile con overlap, cosi'
ogni tile e' embeddato a piena risoluzione e una leader-line / codice a cavallo del
confine resta intero in almeno un tile.

Due livelli, deliberatamente separati:
  - GEOMETRIA PURA (`tile_grid`, `_axis_starts`): dato (larghezza, altezza) in PIXEL,
    calcola i rettangoli `(row, col, x0, y0, w, h)`. Nessun PDF, nessuna immagine:
    matematica testabile in isolamento.
  - I/O (`render_page_tiles`): render `fitz` -> pixmap -> crop dei tile + thumbnail.
    Import pesanti (fitz/PIL) lazy, cosi' la geometria si importa e si testa da sola.

Copertura totale garantita dal CLAMP del tile finale al bordo: l'ultimo start di
ogni asse e' `dim - tile_px`, cosi' i tile hanno dimensione uniforme e insieme
coprono tutta la pagina (l'overlap dell'ultimo tile e' >= di quello nominale).

Design: docs/superpowers/specs/2026-07-09-fase1.1-pixelrag-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass

# Default nell'intervallo approvato (DPI 200-300, overlap ~12%). tile_px = lato del
# tile in pixel (input tipico dell'embedder visivo).
DEFAULT_DPI = 220
DEFAULT_TILE_PX = 1024
DEFAULT_OVERLAP = 0.12
THUMB_MAX_PX = 512


@dataclass(frozen=True)
class Tile:
    """Un tile in coordinate PIXEL del render di pagina (origine in alto a sinistra)."""

    row: int
    col: int
    x0: int
    y0: int
    w: int
    h: int


def _axis_starts(dim: int, tile: int, overlap: float) -> list[int]:
    """Posizioni di partenza dei tile lungo un asse (sliding window + clamp al bordo).

    dim/tile in pixel; overlap in [0, 1). Se la pagina e' piu' piccola del tile, un
    solo tile a 0. Altrimenti passo = tile*(1-overlap); l'ultimo start e' clampato a
    `dim - tile` per chiudere la copertura senza sforare.
    """
    if tile <= 0:
        raise ValueError(f"tile deve essere > 0, ricevuto {tile}")
    if not 0.0 <= overlap < 1.0:
        raise ValueError(f"overlap deve stare in [0, 1), ricevuto {overlap}")
    if dim <= tile:
        return [0]

    stride = max(1, round(tile * (1.0 - overlap)))
    starts = [0]
    while starts[-1] + tile < dim:
        nxt = starts[-1] + stride
        if nxt + tile >= dim:
            nxt = dim - tile   # clamp: l'ultimo tile tocca il bordo
        if nxt == starts[-1]:  # nessun progresso (stride minuscolo): stop difensivo
            break
        starts.append(nxt)
    return starts


def tile_grid(page_w: int, page_h: int, *, tile_px: int = DEFAULT_TILE_PX,
              overlap: float = DEFAULT_OVERLAP) -> list[Tile]:
    """Griglia di tile che copre una pagina di `page_w x page_h` pixel. Pura, testabile.

    I tile hanno dimensione uniforme `min(tile_px, dim)` per asse; l'insieme copre
    tutta la pagina (nessun buco) con l'overlap richiesto. Ordine: riga per riga.
    """
    if page_w <= 0 or page_h <= 0:
        raise ValueError(f"dimensioni pagina non valide: {page_w}x{page_h}")
    xs = _axis_starts(page_w, tile_px, overlap)
    ys = _axis_starts(page_h, tile_px, overlap)
    w = min(tile_px, page_w)
    h = min(tile_px, page_h)
    return [Tile(row=r, col=c, x0=x, y0=y, w=w, h=h)
            for r, y in enumerate(ys) for c, x in enumerate(xs)]


def render_page_tiles(page, *, dpi: int = DEFAULT_DPI, tile_px: int = DEFAULT_TILE_PX,
                      overlap: float = DEFAULT_OVERLAP):
    """Render di una `fitz.Page` -> (lista di (Tile, immagine tile), thumbnail pagina).

    Wrapper I/O: import pesanti lazy. Renderizza la pagina UNA volta a `dpi`, poi
    ritaglia i tile in pixel-space e produce un thumbnail per il contesto globale.
    """
    from io import BytesIO

    from PIL import Image

    pix = page.get_pixmap(dpi=dpi)
    full = Image.open(BytesIO(pix.tobytes("png"))).convert("RGB")

    tiles = tile_grid(full.width, full.height, tile_px=tile_px, overlap=overlap)
    crops = [(t, full.crop((t.x0, t.y0, t.x0 + t.w, t.y0 + t.h))) for t in tiles]

    thumb = full.copy()
    thumb.thumbnail((THUMB_MAX_PX, THUMB_MAX_PX))
    return crops, thumb
