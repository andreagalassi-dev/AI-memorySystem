"""P2.2 — Chunking DIFENSIVO dei record logici dalle tabelle markdown normalizzate.

Un chunk = un record logico (codice canonico intatto + riga intera: pos, descrizione,
quantita', prezzo) ancorato a documento/pagina/tabella/riga per la citazione.
Principio del progetto: ogni chunk emesso e' GIUSTO; tutto cio' che non e'
ricostruibile in modo deterministico finisce in una corsia di scarto ESPLICITA
(reject con motivo) — mai indicizzato silenziosamente sbagliato.

Regole (deterministiche, auditabili — vedi piano P2.2):
  1. Blocchi-tabella: ogni riga-header apre un blocco; gli header non sono mai
     record (neutralizza il failure-mode "intestazioni ripetute").
  2. Doppia tabella affiancata (P231): le colonne CODICE dell'header sono i punti
     di taglio; ogni colonna-codice apre un segmento di record.
  3. Record multi-riga (P060): merge SOLO nel pattern a coppia adiacente
     (riga senza codice + riga con codice); ambiguita' -> reject di entrambe.
  4. Degradazione a livello di campo (qty/prezzo non attribuibili -> None +
     warning), scarto a livello di codice (cella multi-codice, frammenti,
     famiglia non conforme al blocco -> reject).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.ingestion.codes import SHORT_CODE, base_cleanup, cells_rows

# Token gia' canonico della famiglia lunga Mamoli (l'input e' il md NORMALIZZATO
# da P1.5+: i codici lunghi validi sono gia' in forma canonica 'V0'+10 alnum).
LONG_TOKEN = re.compile(r"^V0[0-9A-Z]{10}$")
# Frammento di codice fuori posto in una cella non-codice (es. 'V0 92750 68,00').
FRAGMENT = re.compile(r"V0\s*\d{3,}")
# 'V0' isolato in una cella non-codice (prefisso slittato, es. 'V0 50,00').
STRAY_V0 = re.compile(r"(?<![0-9A-Z])V0(?![0-9A-Z])")

QTY_RE = re.compile(r"^\d+(?:\+\d+)*$")
PRICE_RE = re.compile(r"^\d{1,3}(?:\.\d{3})*,\d{2}$")
QTY_PRICE_RE = re.compile(r"^(\d+(?:\+\d+)*)\s+(\d{1,3}(?:\.\d{3})*,\d{2})$")

# Soglia di dominanza famiglia-codice per blocco: se >=80% dei codici del blocco
# e' famiglia lunga V0, un codice corto nello stesso blocco e' sospetto (frammento).
FAMILY_DOMINANCE = 0.8


# --------------------------------------------------------------------- header/ruoli


def _despace(s: str) -> str:
    """Confronto keyword robusto allo small-caps spezzato ('C ODICE' -> 'codice')."""
    return re.sub(r"\s+", "", base_cleanup(s)).lower()


def classify_header_cell(cell: str) -> str:
    t = _despace(cell)
    if not t:
        return "empty"
    if "codice" in t or "code" in t:
        return "code"
    if "descrizione" in t or "description" in t:
        return "desc"
    qty = any(k in t for k in ("conf", "pcs", "pzx", "pack"))
    price = "euro" in t or "€" in t
    if qty and price:
        return "qty_price"
    if qty:
        return "qty"
    if price:
        return "price"
    if "pos" in t:
        return "pos"
    if "ricambi" in t or "spare" in t:
        return "marker"
    return "unknown"


def is_header_row(cells: list[str]) -> bool:
    roles = [classify_header_cell(c) for c in cells]
    if "code" in roles and "desc" in roles:
        return True
    if roles.count("marker") >= 2:  # riga-titolo 'RICAMBI/SPARE PARTS' ripetuta
        return True
    # Header contaminato/spalmato da Docling (es. P060 riga 47: 'ODICE ODE' spezzato
    # tra celle): niente colonna CODICE riconoscibile, ma la riga e' fatta di
    # keyword-ruolo e non contiene alcun codice valido -> e' un header, non un dato.
    # Il check sui codici ignora le celle gia' riconosciute come keyword-ruolo
    # ('CONF' e' una keyword qty ma matcherebbe anche la regex dei codici corti).
    keyword_cells = sum(1 for r in roles if r not in ("unknown", "empty"))
    has_code = any(parse_code_cell(c)[0] in ("long", "short", "multi_long")
                   for c, r in zip(cells, roles) if r in ("unknown", "empty"))
    return keyword_cells >= 3 and not has_code


@dataclass
class Segment:
    """Un record logico dentro la riga: colonne [code_col..end), ruoli per colonna."""
    label: str                # single | left | right
    code_col: int
    end: int
    pos_col: int | None = None
    desc_cols: list[int] = field(default_factory=list)
    qty_cols: list[int] = field(default_factory=list)
    price_cols: list[int] = field(default_factory=list)
    qty_price_cols: list[int] = field(default_factory=list)


def segments_plausible(segments: list[Segment]) -> bool:
    """Una mappa colonne e' plausibile solo se ogni segmento ha almeno una colonna
    di campo (desc/qty/prezzo). Un segmento fatto della sola colonna-codice e' il
    sintomo di un header spalmato (es. P060 riga 60: 'CODICE...POS' duplicato in due
    celle) che trasformerebbe i POS in codici: mai fidarsi di una mappa degenere."""
    return bool(segments) and all(
        s.desc_cols or s.qty_cols or s.price_cols or s.qty_price_cols for s in segments
    )


def build_segments(roles: list[str]) -> list[Segment]:
    """Split deterministico: ogni colonna CODICE apre un segmento di record."""
    code_cols = [i for i, r in enumerate(roles) if r == "code"]
    segments: list[Segment] = []
    for k, ci in enumerate(code_cols):
        end = code_cols[k + 1] if k + 1 < len(code_cols) else len(roles)
        if len(code_cols) == 1:
            label = "single"
        else:
            label = "left" if k == 0 else f"right{k if k > 1 else ''}"
        seg = Segment(label=label, code_col=ci, end=end)
        if k == 0:
            left_pos = [i for i in range(ci) if roles[i] == "pos"]
            seg.pos_col = left_pos[-1] if left_pos else None
        role_cols = {"desc": seg.desc_cols, "qty": seg.qty_cols,
                     "price": seg.price_cols, "qty_price": seg.qty_price_cols}
        for i in range(ci + 1, end):
            if roles[i] in role_cols:
                role_cols[roles[i]].append(i)
        segments.append(seg)
    return segments


# ------------------------------------------------------------------- parsing celle


def parse_code_cell(cell: str) -> tuple[str, list[str]]:
    """Classifica la cella-codice: (kind, codici).

    kind: 'long' | 'short' | 'empty' | 'multi_long' | 'invalid'.
    """
    tokens = base_cleanup(cell).split()
    longs = [t for t in tokens if LONG_TOKEN.fullmatch(t)]
    if len(longs) >= 2:
        return "multi_long", longs
    if len(longs) == 1:
        return "long", longs
    if not tokens:
        return "empty", []
    if len(tokens) == 1 and SHORT_CODE.match(tokens[0]):
        return "short", tokens
    return "invalid", []


def parse_field(raw: str, kind: str) -> tuple[str | None, list[str], list[str]]:
    """Valida un campo non-codice: (valore|None, warnings, frammenti_codice).

    Mai indovinare: campo non conforme -> None + warning esplicito.
    """
    clean = base_cleanup(raw)
    if not clean:
        return None, [], []
    frags = FRAGMENT.findall(clean)
    if frags:
        return None, [f"{kind}_contains_code_fragment"], frags
    if STRAY_V0.search(clean):
        return None, [f"{kind}_contains_stray_code_prefix"], []
    if kind == "qty":
        return (clean, [], []) if QTY_RE.fullmatch(clean) else (None, ["qty_unparseable"], [])
    if kind == "price":
        return (clean, [], []) if PRICE_RE.fullmatch(clean) else (None, ["price_unparseable"], [])
    return clean, [], []  # desc / pos: testo libero


def parse_qty_price(raw: str) -> tuple[str | None, str | None, list[str], list[str]]:
    """Cella composita 'qty prezzo' (lato destro P231, es. '1 43,00')."""
    clean = base_cleanup(raw)
    if not clean:
        return None, None, [], []
    frags = FRAGMENT.findall(clean)
    if frags:
        return None, None, ["qty_price_contains_code_fragment"], frags
    if STRAY_V0.search(clean):
        return None, None, ["qty_price_contains_stray_code_prefix"], []
    m = QTY_PRICE_RE.fullmatch(clean)
    if m:
        return m.group(1), m.group(2), [], []
    if PRICE_RE.fullmatch(clean):  # solo prezzo, qty assente
        return None, clean, [], []
    return None, None, ["ambiguous_qty_price"], []


# ------------------------------------------------------------------ record grezzo


@dataclass
class RawRecord:
    block: int
    row_index: int          # 1-based sul md sorgente
    segment: str
    code_kind: str
    codes: list[str]
    code_cell: str
    pos: str | None
    desc: str | None
    qty: str | None
    price: str | None
    warnings: list[str]
    fragments: list[str]
    source_cells: list[str]

    @property
    def has_payload(self) -> bool:
        return any(v for v in (self.pos, self.desc, self.qty, self.price))


def extract_raw_record(cells: list[str], seg: Segment, block: int,
                       row_index: int) -> RawRecord:
    kind, codes = parse_code_cell(cells[seg.code_col])
    warnings: list[str] = []
    fragments: list[str] = []

    def fields(cols: list[int], fkind: str) -> str | None:
        vals = []
        for i in cols:
            v, w, f = parse_field(cells[i], fkind)
            warnings.extend(w)
            fragments.extend(f)
            if v:
                vals.append(v)
        return " ".join(vals) if vals else None

    pos = fields([seg.pos_col], "pos") if seg.pos_col is not None else None
    desc = fields(seg.desc_cols, "desc")
    qty = fields(seg.qty_cols, "qty")
    price = fields(seg.price_cols, "price")
    for i in seg.qty_price_cols:
        q, p, w, f = parse_qty_price(cells[i])
        warnings.extend(w)
        fragments.extend(f)
        qty = qty or q
        price = price or p
    # piu' valori nei campi quantita'/prezzo = sospetta riga fusa: la descrizione
    # potrebbe contenere testo di un altro record. Flag esplicito, non silenzio.
    if "ambiguous_qty_price" in warnings and desc:
        warnings.append("desc_multi_record_suspect")
    return RawRecord(block, row_index, seg.label, kind, codes, cells[seg.code_col].strip(),
                     pos, desc, qty, price, warnings, fragments,
                     [cells[i] for i in range(seg.pos_col if seg.pos_col is not None
                                              else seg.code_col, seg.end)])


# ----------------------------------------------------------------------- risultato


@dataclass
class Chunk:
    chunk_id: str
    text: str
    code: str | None       # None = riga legittimamente senza codice (es. DeliTools)
    code_raw: str | None
    normalization_type: str | None
    pos: str | None
    desc: str | None
    qty: str | None
    price: str | None
    doc: str
    page: int                 # 0 per sorgenti XLSX (l'ancora e' `sheet`)
    lang: str
    table_index: int
    row_index: int
    segment: str
    status: str
    warnings: list[str]
    source_cells: list[str]   # celle sorgente del record (audit trail)
    sheet: str | None = None  # foglio XLSX per la citazione (None per i PDF)


@dataclass
class Reject:
    table_id: str
    block: int
    row_index: int
    segment: str
    reason: str
    codes_involved: list[str]
    source_cells: list[str]


def _merge_pair(pending: RawRecord, cur: RawRecord) -> RawRecord | None:
    """Merge deterministico riga-senza-codice + riga-con-codice (P060).

    Ritorna il record fuso oppure None se ambiguo (conflitto su pos/qty/prezzo).
    La descrizione si CONCATENA in ordine di lettura; gli altri campi ammettono
    un solo valore.
    """
    merged_desc = " ".join(v for v in (pending.desc, cur.desc) if v) or None
    out = {}
    for f in ("pos", "qty", "price"):
        a, b = getattr(pending, f), getattr(cur, f)
        if a and b and a != b:
            return None
        out[f] = a or b
    return RawRecord(cur.block, cur.row_index, cur.segment, cur.code_kind, cur.codes,
                     cur.code_cell, out["pos"], merged_desc, out["qty"], out["price"],
                     sorted(set(pending.warnings + cur.warnings)),
                     pending.fragments + cur.fragments,
                     pending.source_cells + cur.source_cells)


def chunk_table(table_id: str, md_text: str, meta: dict,
                norm_map: dict[str, dict] | None = None) -> dict:
    """Chunking difensivo di un markdown tabellare normalizzato.

    Ritorna {"chunks": [Chunk], "rejects": [Reject], "report": {...}}.
    """
    norm_map = norm_map or {}
    rows = cells_rows(md_text)
    chunks: list[Chunk] = []
    rejects: list[Reject] = []
    column_maps: list[dict] = []

    # --- 1. segmentazione in blocchi (header = nuovo blocco, mai record) --------
    blocks: list[dict] = []   # {"segments": [...], "rows": [(row_index, cells)]}
    current: dict | None = None
    in_header_run = False
    for idx, cells in enumerate(rows, start=1):
        if is_header_row(cells):
            roles = [classify_header_cell(c) for c in cells]
            if not in_header_run:
                current = {"segments": None, "roles": None, "rows": [],
                           "inherited": False}
                blocks.append(current)
                in_header_run = True
            if "code" in roles:  # l'header informativo del run definisce i ruoli
                current["segments"] = build_segments(roles)
                current["roles"] = roles
            continue
        in_header_run = False
        if current is None:
            rejects.append(Reject(table_id, len(blocks), idx, "-",
                                  "data_before_header", [], cells))
            continue
        current["rows"].append((idx, cells))

    # Mappa colonne assente o implausibile (header contaminato/spalmato da Docling):
    # NON si indovina dai dati — si eredita la mappa del blocco precedente della
    # stessa pagina (stesso layout), in modo esplicito e loggato. Senza un blocco
    # precedente affidabile, le righe finiscono nei reject (mai indicizzate a caso).
    last_good: dict | None = None
    for blk in blocks:
        if blk["segments"] is not None and segments_plausible(blk["segments"]):
            last_good = blk
            continue
        if last_good is not None:
            blk["segments"] = last_good["segments"]
            blk["roles"] = last_good["roles"]
            blk["inherited"] = True
        else:
            blk["segments"] = None

    # --- 2. estrazione record per blocco/segmento -------------------------------
    raw_by_block: list[list[RawRecord]] = []
    for b, blk in enumerate(blocks, start=1):
        column_maps.append({"block": b, "roles": blk["roles"],
                            "segments": [s.label for s in blk["segments"] or []],
                            "inherited": blk["inherited"]})
        if not blk["segments"]:
            for idx, cells in blk["rows"]:
                rejects.append(Reject(table_id, b, idx, "-", "unusable_header",
                                      [], cells))
            continue
        width = len(blk["roles"])
        block_records: list[RawRecord] = []
        pending: dict[str, RawRecord] = {}   # per segment label
        for idx, cells in blk["rows"]:
            if len(cells) != width:
                rejects.append(Reject(table_id, b, idx, "-", "row_width_mismatch",
                                      [], cells))
                continue
            for seg in blk["segments"]:
                rec = extract_raw_record(cells, seg, b, idx)
                for frag in rec.fragments:
                    rejects.append(Reject(table_id, b, idx, seg.label,
                                          "code_fragment_spillover",
                                          [re.sub(r"\s+", "", f) for f in [frag]],
                                          rec.source_cells))
                if rec.code_kind == "multi_long":
                    rejects.append(Reject(table_id, b, idx, seg.label,
                                          "fused_multi_record_row", rec.codes,
                                          rec.source_cells))
                    continue
                if rec.code_kind == "invalid":
                    rejects.append(Reject(table_id, b, idx, seg.label,
                                          "no_valid_code", [], rec.source_cells))
                    continue
                if rec.code_kind == "empty":
                    if not rec.has_payload:
                        continue  # slot di segmento vuoto: non e' un dato
                    prev = pending.get(seg.label)
                    if prev is not None:
                        rejects.append(Reject(table_id, b, prev.row_index, seg.label,
                                              "orphan_row", [], prev.source_cells))
                    pending[seg.label] = rec
                    continue
                # codice valido (long|short)
                prev = pending.pop(seg.label, None)
                if prev is not None:
                    # merge solo se la riga col codice e' davvero una meta' di
                    # record (le manca almeno un campo): una riga gia' completa
                    # non assorbe mai una riga vagante (che diventa orphan_row).
                    incomplete = not (rec.desc and rec.qty and rec.price)
                    merged = _merge_pair(prev, rec) if incomplete else None
                    if merged is not None:
                        merged.warnings.append("recovered_multirow")
                        block_records.append(merged)
                        continue
                    rejects.append(Reject(table_id, b, prev.row_index, seg.label,
                                          "orphan_row" if not incomplete
                                          else "multirow_merge_conflict",
                                          [], prev.source_cells))
                    if incomplete and merged is None:
                        rejects.append(Reject(table_id, b, rec.row_index, seg.label,
                                              "multirow_merge_conflict", rec.codes,
                                              rec.source_cells))
                        continue
                block_records.append(rec)
        for prev in pending.values():
            rejects.append(Reject(table_id, b, prev.row_index, prev.segment,
                                  "orphan_row", [], prev.source_cells))
        raw_by_block.append(block_records)

    # --- 3. validazione famiglia dominante per blocco ---------------------------
    accepted: list[RawRecord] = []
    for block_records in raw_by_block:
        n_long = sum(1 for r in block_records if r.code_kind == "long")
        total = len(block_records)
        long_share = n_long / total if total else 0.0
        for rec in block_records:
            if rec.code_kind == "short" and long_share >= FAMILY_DOMINANCE:
                rejects.append(Reject(table_id, rec.block, rec.row_index, rec.segment,
                                      "family_mismatch", rec.codes, rec.source_cells))
                continue
            accepted.append(rec)

    # --- 4. emissione chunk ------------------------------------------------------
    for rec in accepted:
        code = rec.codes[0]
        if not rec.desc:
            rec.warnings.append("desc_missing")
        recovered = "recovered_multirow" in rec.warnings
        real_warnings = [w for w in rec.warnings if w != "recovered_multirow"]
        status = ("recovered_multirow" if recovered
                  else "degraded" if real_warnings else "ok")
        text = " | ".join(p for p in (
            f"{meta['doc']} pagina {meta['page']}",
            f"tabella {rec.block}",
            f"pos {rec.pos}" if rec.pos else None,
            f"codice {code}",
            rec.desc,
            f"quantita {rec.qty}" if rec.qty else None,
            f"prezzo {rec.price} EUR" if rec.price else None,
        ) if p)
        nm = norm_map.get(code, {})
        chunks.append(Chunk(
            chunk_id=f"{table_id}/t{rec.block:02d}/r{rec.row_index:03d}/{rec.segment}",
            text=text, code=code,
            code_raw=nm.get("original", rec.code_cell),
            normalization_type=nm.get("type"),
            pos=rec.pos, desc=rec.desc, qty=rec.qty, price=rec.price,
            doc=meta["doc"], page=meta["page"], lang=meta["lang"],
            table_index=rec.block, row_index=rec.row_index, segment=rec.segment,
            status=status, warnings=sorted(set(real_warnings)),
            source_cells=rec.source_cells,
        ))

    # --- 5. report ----------------------------------------------------------------
    reasons: dict[str, int] = {}
    for r in rejects:
        reasons[r.reason] = reasons.get(r.reason, 0) + 1
    warn_counts: dict[str, int] = {}
    for c in chunks:
        for w in c.warnings:
            warn_counts[w] = warn_counts.get(w, 0) + 1
    report = {
        "table_id": table_id,
        "blocks": len(blocks),
        "column_maps": column_maps,
        "data_rows": sum(len(b["rows"]) for b in blocks),
        "chunks": {"total": len(chunks),
                   "ok": sum(1 for c in chunks if c.status == "ok"),
                   "recovered_multirow": sum(1 for c in chunks
                                             if c.status == "recovered_multirow"),
                   "degraded": sum(1 for c in chunks if c.status == "degraded")},
        "rejects": reasons,
        "reject_codes": sorted({cd for r in rejects for cd in r.codes_involved}),
        "chunk_warnings": warn_counts,
    }
    return {"chunks": chunks, "rejects": rejects, "report": report}
