"""Fase 1.2 — Rilevamento pagine-tabella (euristica DETERMINISTICA, no AI).

Individua le pagine listino/tabella/griglia che vanno al binario di estrazione.
NON usa modelli: solo feature geometriche/testuali da PyMuPDF (~ms/pagina,
auditabile: ogni verdetto porta con se' feature e ragione). Stessa separazione di
`page_classifier`: I/O in `table_features` (su `fitz.Page`), decisione PURA in
`classify_table` (su dict), unit-testabile senza PDF.

INDIPENDENTE dalla Fase 1.0 per contratto: sa flaggare una tabella anche su una
pagina born-digital gia' classificata "testuale" dal Fast-Dispatch (la ridondanza
e' un compromesso accettato per disaccoppiare i binari).

Due segnali positivi indipendenti (una tabella ne soddisfa almeno uno):
  - GRIGLIA RIGATA: molte linee orizzontali E verticali (bordi cella da
    `get_drawings`) -> listino/griglia con bordi.
  - BORDERLESS: colonne allineate (stessi x0 su piu' righe) + alta densita' di
    valori numerici/codici -> listino senza bordi (allineamento a colonne).

Soglie calibrate a occhio su casi netti: sono un PRIMO strato, da tarare sul
corpus reale (territorio gate P1). Vivono in THRESHOLDS, esposte nel reason.

Design: docs/superpowers/specs/2026-07-09-fase1.2-estrazione-massiva-design.md.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

THRESHOLDS = {
    # --- segnale GRIGLIA (linee da get_drawings) ---
    "T_HLINES": 3,          # separatori orizzontali minimi per una griglia
    "T_VLINES": 3,          # separatori verticali minimi
    "LINE_TOL": 2.0,        # px: |dy|<=tol -> orizzontale, |dx|<=tol -> verticale
    # --- segnale BORDERLESS (allineamento a colonne + valori) ---
    "T_COLS": 3,            # colonne allineate minime
    "T_ROWS": 3,            # bande-riga minime
    "T_NUMRATIO": 0.20,     # densita' minima di parole valore (numeri/codici)
    "COL_TOL": 3.0,         # px: bucket di allineamento colonna (x0)
    "ROW_TOL": 3.0,         # px: bucket di banda-riga (y0)
    "COL_MIN_MEMBERS": 3,   # parole in uno stesso x0 per contarlo come colonna
    # --- guardia comune ---
    "MIN_WORDS": 6,         # sotto: pagina troppo scarna per essere una tabella
}

# Parola "valore": numero/prezzo/quantita' (inizia per cifra, solo cifre+sep) o
# codice (mix cifre+lettere). Deterministico, nessun catalogo.
_VALUE_NUM = re.compile(r"^\d[\d.,/-]*$")


@dataclass(frozen=True)
class TableVerdict:
    """Esito del rilevamento tabella per UNA pagina."""

    is_table: bool
    reason: str        # spiegazione auditabile (segnale decisivo + feature)
    features: dict     # feature grezze usate (audit)


def _is_value_word(tok: str) -> bool:
    """True se la parola somiglia a un valore di tabella (numero/prezzo o codice)."""
    t = tok.strip()
    if not t:
        return False
    if _VALUE_NUM.match(t):
        return True
    return any(c.isdigit() for c in t) and any(c.isalpha() for c in t)


def table_features(page, t: dict = THRESHOLDS) -> dict:
    """Feature deterministiche da un `fitz.Page` (qui l'I/O, le regole altrove)."""
    line_tol = t["LINE_TOL"]
    h_lines = v_lines = 0
    for d in page.get_drawings():
        for item in d.get("items", []):
            kind = item[0]
            if kind == "l" and len(item) >= 3:
                p0, p1 = item[1], item[2]
                dx, dy = abs(p0.x - p1.x), abs(p0.y - p1.y)
                if dy <= line_tol and dx > line_tol:
                    h_lines += 1
                elif dx <= line_tol and dy > line_tol:
                    v_lines += 1
            elif kind == "re" and len(item) >= 2:
                rect = item[1]
                # un rettangolo (bordo cella/box) = 2 bordi h + 2 bordi v
                if rect.width > line_tol:
                    h_lines += 2
                if rect.height > line_tol:
                    v_lines += 2

    words = page.get_text("words")  # (x0, y0, x1, y1, parola, block, line, word)
    n_words = len(words)
    col_tol, row_tol = t["COL_TOL"], t["ROW_TOL"]
    x_buckets = Counter(round(w[0] / col_tol) * col_tol for w in words)
    y_buckets = {round(w[1] / row_tol) * row_tol for w in words}
    n_cols = sum(1 for _, c in x_buckets.items() if c >= t["COL_MIN_MEMBERS"])
    n_rows = len(y_buckets)
    value_words = sum(1 for w in words if _is_value_word(w[4]))
    numeric_ratio = round(value_words / n_words, 4) if n_words else 0.0

    return {"h_lines": h_lines, "v_lines": v_lines, "n_words": n_words,
            "n_cols": n_cols, "n_rows": n_rows, "numeric_ratio": numeric_ratio}


def classify_table(f: dict, t: dict = THRESHOLDS) -> TableVerdict:
    """Regole esplicite sulle feature. Pure function, unit-testabile senza PDF."""
    has_content = f["n_words"] >= t["MIN_WORDS"]

    grid = has_content and f["h_lines"] >= t["T_HLINES"] and f["v_lines"] >= t["T_VLINES"]
    columnar = (has_content
                and f["n_cols"] >= t["T_COLS"]
                and f["n_rows"] >= t["T_ROWS"]
                and f["numeric_ratio"] >= t["T_NUMRATIO"])

    if grid:
        return TableVerdict(True, f"griglia rigata: h_lines={f['h_lines']}, "
                            f"v_lines={f['v_lines']}", f)
    if columnar:
        return TableVerdict(True, f"tabella borderless: n_cols={f['n_cols']}, "
                            f"n_rows={f['n_rows']}, numeric_ratio={f['numeric_ratio']}", f)
    if not has_content:
        return TableVerdict(False, f"pagina troppo scarna: n_words={f['n_words']}", f)
    return TableVerdict(False, f"nessun segnale-tabella: h_lines={f['h_lines']}, "
                        f"v_lines={f['v_lines']}, n_cols={f['n_cols']}, "
                        f"numeric_ratio={f['numeric_ratio']}", f)


def is_table_page(page) -> TableVerdict:
    """Convenience: estrae le feature dalla pagina e la classifica."""
    return classify_table(table_features(page))
