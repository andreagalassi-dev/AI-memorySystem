"""P2.4 — Adapter difensivo per le tabelle CSV estratte dagli XLSX (P1 baseline).

Stessa filosofia del chunker markdown (P2.2): un chunk = un record logico,
righe banner/preambolo scartate in modo esplicito, campi non conformi degradati
a None con warning — mai indovinati. Il mapping colonne e' ESPLICITO per-tabella
(registro CSV_TABLES): l'onboarding di una nuova sorgente e' configurazione,
non euristica.

Casi coperti dal corpus reale:
- DeliTools: banner iniziale, colonna Part Number VUOTA su tutte le righe
  (identificazione per pos+modello: chunk senza codice, retrieval solo
  semantico), descrizione cinese ignorata come testo di ricerca (resta in
  source_cells per l'audit).
- Electrolux EN/ES: preambolo Model/PNC, header per lingua, pos talvolta vuoto
  (0USDSC/0USDSD): il codice resta l'identita' del record.
"""

from __future__ import annotations

import csv
import re
import unicodedata

from src.ingestion.chunking import Chunk, Reject
from src.ingestion.codes import INVISIBLE


def csv_cleanup(s: str) -> str:
    """Cleanup per celle CSV: NFKC, invisibili, spazi — SENZA rimozione markup.

    base_cleanup (markdown) toglie `*` come markup; nei CSV da XLSX l'asterisco
    e' contenuto letterale (filettature 'M6*16'): il contratto GT ha catturato
    la corruzione 'M6*16'->'M616' al primo run.
    """
    s = unicodedata.normalize("NFKC", s or "")
    s = INVISIBLE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()

QTY_CSV_RE = re.compile(r"^\d+(?:\+\d+)*$")
CODE_CSV_RE = re.compile(r"^[0-9A-Z][0-9A-Z./-]{1,11}$")  # token singolo, no spazi

# Registro sorgenti CSV: header attesi -> ruoli. `required` individua la riga
# header (tutte le colonne richieste presenti); tutto cio' che precede e' banner.
CSV_TABLES: dict[str, dict] = {
    "DELITOOLS_EN": {
        "path": ("output/spike/baseline/DeliTools_SpareParts_List/csv/"
                 "DeliTools_SpareParts_List__Sheet1.csv"),
        "doc": "DeliTools_SpareParts_List.xlsx",
        "sheet": "Sheet1",
        "lang": "EN",
        "columns": {"pos": "Part Positional Number", "code": "Part Number",
                    "desc": "Description - English", "qty": "Bom Q'ty"},
        "required": ["Part Positional Number", "Description - English"],
    },
    "ELECTROLUX_EN": {
        "path": ("output/spike/baseline/Electrolux_Distinta_Ricambi/csv/"
                 "Electrolux_Distinta_Ricambi__PARTS_LIST_-_ENGLISH.csv"),
        "doc": "Electrolux_Distinta_Ricambi.xlsx",
        "sheet": "PARTS LIST - ENGLISH",
        "lang": "EN",
        "columns": {"pos": "NO", "code": "COMPONENT PNC",
                    "desc": "DESCRIPTION", "qty": "QTY"},
        "required": ["NO", "DESCRIPTION", "COMPONENT PNC"],
    },
    "ELECTROLUX_ES": {
        "path": ("output/spike/baseline/Electrolux_Distinta_Ricambi/csv/"
                 "Electrolux_Distinta_Ricambi__PARTS_LIST_-_SPANISH.csv"),
        "doc": "Electrolux_Distinta_Ricambi.xlsx",
        "sheet": "PARTS LIST - SPANISH",
        "lang": "ES",
        "columns": {"pos": "NO", "code": "COMPONENTE PNC",
                    "desc": "DESCRIPCIÓN", "qty": "QTY"},
        "required": ["NO", "DESCRIPCIÓN", "COMPONENTE PNC"],
    },
}


def _find_header(rows: list[list[str]], required: list[str]) -> tuple[int, dict[str, int]]:
    """Prima riga che contiene TUTTE le colonne richieste -> (indice, nome->col)."""
    for i, row in enumerate(rows):
        cells = [csv_cleanup(c) for c in row]
        if all(r in cells for r in required):
            return i, {name: cells.index(name) for name in cells if name}
    raise ValueError(f"header non trovato (attese colonne: {required})")


def chunk_csv_table(table_id: str, csv_text: str, cfg: dict) -> dict:
    """Chunking difensivo di una tabella CSV. Ritorna {chunks, rejects, report}."""
    rows = list(csv.reader(csv_text.splitlines()))
    hdr_idx, name_to_col = _find_header(rows, cfg["required"])
    colmap = {role: name_to_col[name] for role, name in cfg["columns"].items()
              if name in name_to_col}

    chunks: list[Chunk] = []
    rejects: list[Reject] = []
    banner_rows = hdr_idx
    empty_rows = 0

    for idx, row in enumerate(rows[hdr_idx + 1:], start=hdr_idx + 2):  # 1-based
        def get(role: str, row=row) -> str:
            if role in colmap and colmap[role] < len(row):
                return csv_cleanup(row[colmap[role]])
            return ""

        pos, code, desc, qty = get("pos"), get("code"), get("desc"), get("qty")
        if not any((pos, code, desc, qty)):
            empty_rows += 1
            continue
        if not desc and not code:
            rejects.append(Reject(table_id, 1, idx, "csv", "no_identity",
                                  [], [c for c in row if c]))
            continue
        warnings: list[str] = []
        if code and not CODE_CSV_RE.fullmatch(code):
            rejects.append(Reject(table_id, 1, idx, "csv", "malformed_code",
                                  [code], [c for c in row if c]))
            continue
        if qty and not QTY_CSV_RE.fullmatch(qty):
            warnings.append("qty_unparseable")
            qty = ""
        if not desc:
            warnings.append("desc_missing")

        text = " | ".join(p for p in (
            f"{cfg['doc']} foglio {cfg['sheet']}",
            f"pos {pos}" if pos else None,
            f"codice {code}" if code else None,
            desc or None,
            f"quantita {qty}" if qty else None,
        ) if p)
        chunks.append(Chunk(
            chunk_id=f"{table_id}/r{idx:03d}",
            text=text, code=code or None, code_raw=code or None,
            normalization_type=None,
            pos=pos or None, desc=desc or None, qty=qty or None, price=None,
            doc=cfg["doc"], page=0, lang=cfg["lang"],
            table_index=1, row_index=idx, segment="csv",
            status="degraded" if warnings else "ok",
            warnings=warnings, source_cells=[c for c in row if c],
            sheet=cfg["sheet"],
        ))

    report = {
        "table_id": table_id,
        "header_row": hdr_idx + 1,
        "banner_rows": banner_rows,
        "empty_rows": empty_rows,
        "chunks": {"total": len(chunks),
                   "ok": sum(1 for c in chunks if c.status == "ok"),
                   "degraded": sum(1 for c in chunks if c.status == "degraded"),
                   "senza_codice": sum(1 for c in chunks if c.code is None)},
        "rejects": {r.reason: sum(1 for x in rejects if x.reason == r.reason)
                    for r in rejects},
    }
    return {"chunks": chunks, "rejects": rejects, "report": report}
