# /// script
# requires-python = ">=3.11"
# dependencies = ["pymupdf>=1.24"]
# ///
"""P1.4-light — Rigenera le descrizioni del ground truth dal TESTO NATIVO.

Il GT di MAMOLI_P231 aveva le descrizioni prese dal Markdown baseline (mangled:
'aCCeSSori FiSSaGGiodeviatore'). Il text-layer nativo e' invece pulito
('Accessori fissaggio Deviatore/Diverter'). Qui rigeneriamo la colonna
`descrizione` ancorando ogni riga al suo CODICE: parole sulla stessa riga a
destra del codice, fino alla colonna prezzo (pagina a 2 tabelle affiancate),
togliendo la quantita' finale.

NON tocca codice/pos/quantita/prezzo (gia' validati a mano). Solo MAMOLI_P231:
le altre tabelle GT hanno descrizioni gia' pulite (testo nativo non mangled).
Backup dell'originale in expected.pre_descfix.csv.

Uso: uv run scripts/regenerate_gt_descriptions.py
"""

from __future__ import annotations

import csv
import os
import re
import shutil
from pathlib import Path

import fitz  # PyMuPDF

ROOT = Path(__file__).resolve().parent.parent
CORPUS = Path(os.environ.get("CORPUS_DIR", ROOT / "corpus"))
GT = ROOT / "data" / "ground_truth"

# (table_id, file, pagina 1-based, colonna descrizione) — solo le tabelle mangled
TARGETS = [
    ("MAMOLI_P231_IT", "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf", 231, "descrizione"),
]

PRICE = re.compile(r"^\d{1,4}[.,]\d{2}$")
QTY = re.compile(r"^[\d+]+$")


def normcode(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z]", "", s or "")


def build_desc_map(pdf: Path, page: int) -> dict[str, str]:
    doc = fitz.open(str(pdf))
    if doc.needs_pass:
        doc.authenticate("")
    words = doc.load_page(page - 1).get_text("words")
    doc.close()
    by_code: dict[str, str] = {}
    for w in words:
        code = normcode(w[4])
        if len(code) < 6:  # solo token abbastanza lunghi da essere codici
            continue
        x1, yc = w[2], (w[1] + w[3]) / 2
        same = sorted((v for v in words if v[1] <= yc <= v[3] and v[0] > x1 - 1),
                      key=lambda v: v[0])
        toks: list[str] = []
        for v in same:
            if PRICE.match(v[4]):
                break
            toks.append(v[4])
        if toks and QTY.match(toks[-1]):  # via UNA quantita' finale (non i model-ref)
            toks.pop()
        by_code[code] = " ".join(toks).strip()
    return by_code


def main() -> int:
    for table_id, fname, page, col in TARGETS:
        d = GT / table_id
        exp = d / "expected.csv"
        rows = list(csv.DictReader(exp.open(encoding="utf-8")))
        fields = rows[0].keys() if rows else []
        desc_map = build_desc_map(CORPUS / fname, page)

        shutil.copyfile(exp, d / "expected.pre_descfix.csv")
        updated = missed = 0
        for r in rows:
            code = normcode(r.get("codice", ""))
            new = desc_map.get(code)
            if new:
                if new != r.get(col, ""):
                    updated += 1
                r[col] = new
            else:
                missed += 1

        with exp.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(fields))
            w.writeheader()
            w.writerows(rows)
        print(f"[gt-desc] {table_id}: righe={len(rows)} aggiornate={updated} "
              f"codici_non_trovati={missed}  (backup expected.pre_descfix.csv)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
