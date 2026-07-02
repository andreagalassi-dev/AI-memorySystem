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
    """Ritorna (codici_canonici, log_entries) per una cella tabellare."""
    clean = base_cleanup(cell)
    codes: list[str] = []
    logs: list[dict] = []

    longs = LONG_CODE.findall(clean)
    if longs:
        for m in longs:
            can = canonical(m)
            had_split = bool(re.search(r"[\s*_`]", m.strip())) or len(longs) > 1
            logs.append({
                "original": cell.strip(), "normalized": can,
                "type": "safe_rejoin" if had_split else "safe_exact_cleanup",
                "reason": ("codice-doppio fuso: split via findall" if len(longs) > 1
                           else "famiglia V0: uniti blocchi separati da spazi/markup"),
            })
            codes.append(can)
        return codes, logs

    # token corto p060 (gia' esatto nel nativo): solo cleanup
    if SHORT_CODE.match(clean):
        logs.append({"original": cell.strip(), "normalized": clean,
                     "type": "safe_exact_cleanup", "reason": "codice corto: trim/markup"})
        return [clean], logs

    # candidato prefisso perso: numerico lungo che POTREBBE essere un V0... (rule C/D)
    only = canonical(clean)
    if re.fullmatch(r"\d{10,11}", only):
        logs.append({"original": cell.strip(), "normalized": None,
                     "type": "unsafe_candidate",
                     "reason": "numerico lungo senza V0: possibile prefisso perso, NON applicato"})
    return [], logs


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
