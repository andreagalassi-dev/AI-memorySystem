"""Primitive DETERMINISTICHE sui codici ricambio (estratte da P1.5+).

Refactor P2.2: queste funzioni/regex vivevano in `scripts/normalize_codes_docling.py`
(normalizzatore del gate P1) e servono identiche anche al chunker. Comportamento
INVARIATO rispetto a P1: la regressione e' verificata rieseguendo il normalizzatore
e confrontando gli output byte-per-byte. Nessun LLM, nessuna euristica opaca.
"""

from __future__ import annotations

import re
import unicodedata

# Famiglia-codice lunga Mamoli: 'V0' + 10 caratteri alfanumerici (maiuscoli/cifre),
# con spazi/markup interni tollerati. findall separa i codici doppi in una cella.
LONG_CODE = re.compile(r"V0(?:[\s*_`]*[0-9A-Z]){10}")
# Famiglia corta (p060): token alfanumerico 2-8 char, gia' pulito nel nativo.
# Ammette . - / (es. codici 'N.D.', '2500-A-VH-S', '0100').
SHORT_CODE = re.compile(r"^[0-9A-Z][0-9A-Z./-]{1,9}$")
# Caratteri invisibili (soft hyphen, zero-width U+200B-U+200F, LS/PS, BOM).
INVISIBLE = re.compile("[­​-‏  ﻿]")
SEP_ROW = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


def base_cleanup(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    s = INVISIBLE.sub("", s)
    s = re.sub(r"<br\s*/?>|</?[a-zA-Z]+>", " ", s)   # <br>, <b>, </b>, ...
    s = re.sub(r"[*_`]", "", s)
    return re.sub(r"\s+", " ", s).strip()


def canonical(s: str) -> str:
    return re.sub(r"[^0-9A-Z]", "", s.upper())


def normalize_cell(cell: str) -> tuple[list[str], list[dict]]:
    """Ritorna (codici_canonici, log_entries) per una cella tabellare.

    P2.6: wrapper retrocompatibile sul motore a profili (config/brands.json),
    col profilo 'mamoli' che replica byte-per-byte il comportamento P1.5+.
    """
    from src.ingestion.brands import get_profile
    from src.ingestion.brands import normalize_cell as brands_normalize

    return brands_normalize(cell, get_profile("mamoli"))


def cells_rows(md: str) -> list[list[str]]:
    rows = []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|") and not SEP_ROW.match(s):
            rows.append([c.strip() for c in s.strip("|").split("|")])
    return rows


def detect_fused(rows: list[list[str]]) -> dict:
    """Flag failure mode Docling: due tabelle affiancate fuse (header CODICE duplicato)."""
    widths = [len(r) for r in rows]
    modal = max(set(widths), key=widths.count) if widths else 0
    dup_header = any(
        sum("codice" in c.lower() or "code" in c.lower() for c in r) >= 2 for r in rows
    )
    return {"modal_cols": modal, "duplicate_code_header": dup_header,
            "fused_suspected": bool(dup_header or modal > 5)}
