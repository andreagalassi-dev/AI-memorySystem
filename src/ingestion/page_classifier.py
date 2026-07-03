"""P2.5 — Classificatore di pagine: testuale/tabellare (Docling) vs grafica (ColPali).

Euristica DETERMINISTICA su feature PyMuPDF (~ms/pagina, zero GPU, auditabile:
ogni label porta con se' le feature e la ragione). Niente VLM: criterio
d'ingresso scritto nel report (solo se accuratezza binaria < 0.90 sul campione
etichettato, e solo sulla banda incerta).

Le classi vivono su DUE assi indipendenti (misurato sul corpus, 54 PDF):
  - ricchezza testuale (chars del text-layer)
  - carica grafica (drawing paths vettoriali; area immagini raster)
Trappole coperte (casi reali):
  - tabelle con centinaia di drawings (bordi griglia) -> TESTUALE;
  - scansioni con text-layer (100% immagine ma testo estraibile) -> TESTUALE;
  - pagine miste (tabella + esploso, es. Silca p2) -> testuale + flag `dual`
    (vanno a ENTRAMBE le pipeline);
  - scansioni senza text-layer -> grafica + flag `no_text_layer` (decisione
    OCR a P2.6, non materiale ColPali "nativo").

Le soglie sono calibrate sul campione dev di `data/page_labels/labels_v0.csv`
e misurate sull'holdout (mai visto in calibrazione): vedi docs/p2-metrics
sezione P2.5 e tests/test_page_classifier.py.
"""

from __future__ import annotations

from dataclasses import dataclass

# Soglie calibrate sul campione DEV (meta' del labeled set, split deterministico);
# la misura ufficiale e' sull'HOLDOUT mai usato in calibrazione.
THRESHOLDS = {
    "T_CHARS": 300,        # testo "ricco": Docling ha materia su cui lavorare
    "T_CHARS_LOW": 150,    # sotto: basta poca grafica per classificare grafica
    "T_DRAW_EXTREME": 10000,  # esplosi/wiring vettoriali certi
    "T_DRAW_HEAVY": 1000,  # carica vettoriale alta su pagina con testo -> dual
    "T_DRAW_SOFT": 300,    # con poco testo: render/copertine vettoriali
    "RATIO_DRAW": 2.0,     # draw >= RATIO*chars: il disegno domina la pagina mista
    "T_IMG": 0.45,         # copertura raster alta
    "T_IMG_LOW": 0.10,     # copertura raster minima per pagine quasi senza testo
    "T_NIMG": 30,          # molte immagini piccole (esplosi raster a tasselli)
    "NTL_CHARS": 50,       # sotto questi chars, con segnale grafico...
    "NTL_IMG": 0.50,       # ...raster pieno o vettoriale (testo-outline CAD):
    "NTL_DRAW": 300,       # scansione/vector-text senza text-layer -> corsia OCR
    # v2 (P2.8a, punto cieco scoperto dall'eval visiva P2.7):
    "T_ITEMS_EXTREME": 20000,  # segmenti vettoriali (items nei path): esplosi
    "T_ITEMS_HEAVY": 4000,     # con pochi oggetti-path grandi (branson p68)
    "T_BAND_IMG": 0.10,        # banda: raster moderato + testo moderato =
    "T_BAND_CHARS": 350,       # esploso con didascalia/cover (11/12 nel GT banda)
}


@dataclass
class PageClass:
    label: str            # "testuale" | "grafica"
    dual: bool            # True: mandare ANCHE a ColPali (mista testo+grafica)
    no_text_layer: bool   # True: scansione muta -> corsia OCR (P2.6)
    low_content: bool     # True: pagina quasi vuota (ne' testo ne' grafica)
    reason: str           # spiegazione auditabile della decisione


def page_features(page) -> dict:
    """Feature per-pagina da un oggetto fitz.Page (I/O qui, regole altrove)."""
    text = page.get_text()
    area = abs(page.rect) or 1.0
    imgs = page.get_image_info()
    import fitz

    img_area = sum(abs(fitz.Rect(im["bbox"])) for im in imgs) / area
    drawings = page.get_drawings()
    return {
        "chars": len("".join(text.split())),
        "n_images": len(imgs),
        "img_area_ratio": round(min(img_area, 1.0), 4),
        "n_drawings": len(drawings),
        # v2: SEGMENTI dentro i path (un esploso puo' essere 54 oggetti-path
        # da migliaia di segmenti: contare gli oggetti sottostima — punto
        # cieco P2.5 trovato dall'eval visiva P2.7)
        "n_draw_items": sum(len(d["items"]) for d in drawings),
    }


def classify(f: dict, t: dict = THRESHOLDS) -> PageClass:
    """Regole esplicite sulle feature. Pure function, unit-testabile."""
    chars, draw = f["chars"], f["n_drawings"]
    img, nimg = f["img_area_ratio"], f["n_images"]
    items = f.get("n_draw_items", 0)

    text_rich = chars >= t["T_CHARS"]
    graphic_strong = (
        draw >= t["T_DRAW_EXTREME"]
        or items >= t["T_ITEMS_EXTREME"]
        or (img >= t["T_IMG"] and chars < t["T_CHARS"])
        or nimg >= t["T_NIMG"]
    )
    no_text_layer = (chars < t["NTL_CHARS"]
                     and (img >= t["NTL_IMG"] or draw >= t["NTL_DRAW"]))

    if text_rich:
        heavy = (graphic_strong or draw >= t["T_DRAW_HEAVY"]
                 or items >= t["T_ITEMS_HEAVY"])
        if heavy and max(draw, items / 10) >= t["RATIO_DRAW"] * chars:
            # il disegno domina (wiring/esploso con molte etichette):
            # primaria grafica, ma il text-layer c'e' -> anche Docling
            return PageClass("grafica", True, False, False,
                             f"mista a dominanza grafica: draw={draw} >= "
                             f"{t['RATIO_DRAW']}x chars={chars} -> ColPali + Docling")
        if heavy:
            return PageClass("testuale", True, False, False,
                             f"mista a dominanza testuale: chars={chars}, "
                             f"draw={draw}, img={img} -> Docling + ColPali")
        return PageClass("testuale", False, False, False,
                         f"text-layer ricco: chars={chars}")

    graphic_soft = (draw >= t["T_DRAW_SOFT"]
                    or (img >= t["T_IMG_LOW"] and chars < t["T_CHARS_LOW"]))
    # v2 — regola di banda (punto cieco P2.5): raster moderato + testo moderato
    # = esploso con didascalia / cover / tavola con mini-tabella (11/12 nel GT
    # di banda). dual: il poco testo (mini-tabelle con codici) resta a Docling.
    band = img >= t["T_BAND_IMG"] and 100 <= chars < t["T_BAND_CHARS"]
    if graphic_strong or graphic_soft or band:
        dual = band and chars >= 100
        return PageClass("grafica", dual, no_text_layer, False,
                         f"grafica: draw={draw}, items={items}, img_area={img}, "
                         f"chars={chars}"
                         + (" [banda raster+testo moderati -> anche Docling]"
                            if band else "")
                         + (" [senza text-layer -> corsia OCR]" if no_text_layer
                            else ""))
    return PageClass("testuale", False, False, True,
                     f"quasi vuota: chars={chars}, nessuna carica grafica")
