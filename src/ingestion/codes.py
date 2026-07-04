"""Primitive DETERMINISTICHE sui codici ricambio (estratte da P1.5+).

Refactor P2.2: queste funzioni/regex vivevano in `scripts/normalize_codes_docling.py`
(normalizzatore del gate P1) e servono identiche anche al chunker. Comportamento
INVARIATO rispetto a P1: la regressione e' verificata rieseguendo il normalizzatore
e confrontando gli output byte-per-byte. Nessun LLM, nessuna euristica opaca.
"""

from __future__ import annotations

import re
import unicodedata

# Famiglia corta (p060): token alfanumerico 2-8 char, gia' pulito nel nativo.
# Ammette . - / (es. codici 'N.D.', '2500-A-VH-S', '0100').
# Debito §1 residuo: usata SOLO da chunking.py (rilevamento colonna-codice);
# le famiglie per-brand vivono nei Profili di Ingestion a DB (brands.py).
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
