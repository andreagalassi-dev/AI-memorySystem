"""P2.6 — Chunking generalizzato dei markdown estratti dal corpus (54 brand).

Due tipi di chunk per pagina:
  - `table_row`: righe di tabelle markdown in cui una COLONNA-CODICE e'
    rilevabile (>=50% delle celle matcha il profilo brand su >=2 righe);
    il rilevamento cella usa `brands.normalize_cell` (stesso motore del
    normalizzatore: rejoin dove il profilo lo consente, passthrough altrove).
  - `text`: paragrafi del testo non tabellare (max ~800 char, split solo su
    confini di riga: i codici nel testo non vengono MAI spezzati). Le tabelle
    senza colonna-codice riconoscibile diventano chunk `text` riga-per-riga
    raggruppati (contenuto ricercabile, nessun record inventato).

GUARDIA UNIVERSALE (senza GT): ogni `code` emesso deve comparire in forma
canonica nel testo canonico della pagina sorgente -> altrimenti reject
`code_not_in_source`. Nessun codice puo' essere fabbricato dall'estrazione.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.ingestion.brands import normalize_cell
from src.ingestion.codes import base_cleanup, canonical
from src.ingestion.chunking import QTY_RE, PRICE_RE

MAX_TEXT_CHUNK = 800
MIN_TEXT_CHUNK = 40
CODE_COL_SHARE = 0.5
CODE_COL_MIN_HITS = 2


@dataclass
class CorpusChunk:
    chunk_id: str
    type: str                # table_row | text
    text: str
    code: str | None
    pos: str | None
    desc: str | None
    qty: str | None
    price: str | None
    doc: str
    page: int
    lang: str
    cat: str
    status: str
    warnings: list[str] = field(default_factory=list)
    source_cells: list[str] = field(default_factory=list)


def _table_blocks(md: str) -> tuple[list[list[list[str]]], list[str]]:
    """Separa blocchi-tabella (matrici di celle) e righe di testo libero."""
    blocks, text_lines, current = [], [], []
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            if not re.match(r"^\|[\s:|-]+\|$", s):
                current.append([c.strip() for c in s.strip("|").split("|")])
        else:
            if current:
                blocks.append(current)
                current = []
            text_lines.append(line)
    if current:
        blocks.append(current)
    return blocks, text_lines


def _detect_code_column(matrix: list[list[str]], profile: dict) -> int | None:
    width = max(len(r) for r in matrix)
    best, best_hits = None, 0
    for col in range(width):
        hits = nonempty = 0
        for row in matrix:
            cell = row[col] if col < len(row) else ""
            if not cell.strip():
                continue
            nonempty += 1
            codes, _ = normalize_cell(cell, profile)
            hits += len(codes) == 1
        if nonempty and hits >= CODE_COL_MIN_HITS and hits / nonempty >= CODE_COL_SHARE:
            if hits > best_hits:
                best, best_hits = col, hits
    return best


def _desc_column(matrix: list[list[str]], skip: set[int]) -> int | None:
    width = max(len(r) for r in matrix)
    lengths = {c: [] for c in range(width) if c not in skip}
    for row in matrix:
        for c in lengths:
            if c < len(row) and row[c].strip():
                lengths[c].append(len(base_cleanup(row[c])))
    avg = {c: sum(v) / len(v) for c, v in lengths.items() if v}
    return max(avg, key=lambda c: avg[c]) if avg else None


def chunk_page(doc: str, cat: str, page: int, md: str, profile: dict,
               lang: str) -> dict:
    """Chunking difensivo di una pagina markdown estratta."""
    chunks: list[CorpusChunk] = []
    rejects: list[dict] = []
    page_canon = canonical(md)

    def guard_ok(code: str) -> bool:
        return canonical(code) in page_canon

    blocks, text_lines = _table_blocks(md)

    for ti, matrix in enumerate(blocks, start=1):
        code_col = _detect_code_column(matrix, profile)
        if code_col is None:
            # tabella senza colonna-codice: contenuto ricercabile come testo
            for start in range(0, len(matrix), 6):
                rows_txt = " | ".join(
                    " ".join(base_cleanup(c) for c in r if c.strip())
                    for r in matrix[start:start + 6])
                if len(rows_txt) >= MIN_TEXT_CHUNK:
                    chunks.append(CorpusChunk(
                        chunk_id=f"{doc}/p{page:04d}/t{ti}/g{start // 6}",
                        type="text", text=rows_txt[:MAX_TEXT_CHUNK * 2],
                        code=None, pos=None, desc=rows_txt[:MAX_TEXT_CHUNK],
                        qty=None, price=None, doc=doc, page=page, lang=lang,
                        cat=cat, status="ok",
                        warnings=["table_without_code_column"]))
            continue
        desc_col = _desc_column(matrix, {code_col})
        for ri, row in enumerate(matrix):
            cell = row[code_col] if code_col < len(row) else ""
            codes, logs = normalize_cell(cell, profile)
            if len(codes) > 1:
                rejects.append({"doc": doc, "page": page, "table": ti, "row": ri,
                                "reason": "fused_multi_code_cell",
                                "codes": codes, "cells": row})
                continue
            if not codes:
                continue  # riga header o riga senza codice: non e' un record
            code = codes[0]
            if not guard_ok(code):
                rejects.append({"doc": doc, "page": page, "table": ti, "row": ri,
                                "reason": "code_not_in_source",
                                "codes": [code], "cells": row})
                continue
            desc = (base_cleanup(row[desc_col])
                    if desc_col is not None and desc_col < len(row) else None) or None
            pos = qty = price = None
            for c_idx, c in enumerate(row):
                if c_idx in (code_col, desc_col):
                    continue
                v = base_cleanup(c)
                if not v:
                    continue
                # posizionale: i numerici a SINISTRA del codice sono pos/ref,
                # quelli a DESTRA della descrizione sono quantita'
                if c_idx < code_col and pos is None and len(v) <= 4:
                    pos = v
                elif c_idx > (desc_col if desc_col is not None else code_col):
                    if qty is None and QTY_RE.fullmatch(v):
                        qty = v
                    elif price is None and PRICE_RE.fullmatch(v):
                        price = v
            warnings = [] if desc else ["desc_missing"]
            text = " | ".join(p for p in (
                f"{doc} pagina {page}", f"pos {pos}" if pos else None,
                f"codice {code}", desc,
                f"quantita {qty}" if qty else None,
                f"prezzo {price} EUR" if price else None) if p)
            chunks.append(CorpusChunk(
                chunk_id=f"{doc}/p{page:04d}/t{ti}/r{ri:03d}",
                type="table_row", text=text, code=code, pos=pos, desc=desc,
                qty=qty, price=price, doc=doc, page=page, lang=lang, cat=cat,
                status="degraded" if warnings else "ok", warnings=warnings,
                source_cells=[c for c in row if c.strip()]))

    # ------- testo libero -> paragrafi (mai spezzare a meta' riga/token) -------
    paras, buf = [], []
    for line in text_lines:
        if not line.strip():
            if buf:
                paras.append("\n".join(buf))
                buf = []
            continue
        buf.append(line.strip())
        if sum(len(x) for x in buf) >= MAX_TEXT_CHUNK:
            paras.append("\n".join(buf))
            buf = []
    if buf:
        paras.append("\n".join(buf))
    for pi, para in enumerate(paras):
        clean = base_cleanup(para)
        if len(clean) < MIN_TEXT_CHUNK:
            continue
        chunks.append(CorpusChunk(
            chunk_id=f"{doc}/p{page:04d}/x{pi:03d}", type="text",
            text=clean[:MAX_TEXT_CHUNK * 2], code=None, pos=None,
            desc=clean[:MAX_TEXT_CHUNK], qty=None, price=None,
            doc=doc, page=page, lang=lang, cat=cat, status="ok"))
    return {"chunks": chunks, "rejects": rejects}
