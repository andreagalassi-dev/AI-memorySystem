# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""P1.3/P1.6 — Valuta l'estrazione tabelle contro il ground truth.

Confronta l'output di un parser (marker | baseline) con i data/ground_truth/*/expected.csv
secondo docs/p1-metrics.md e produce i due numeri DISAGGREGATI PER LINGUA:
  - Table Cell Accuracy  (strict = cella esatta normalizzata; loose = valore contenuto nella riga)
  - Structure Fidelity Score (0/1/2 per tabella)
oltre a row_recall e false_positive_rows.

Allineamento: key-based sul `codice` normalizzato (fallback `pos`/`ref` se codice vuoto).
Il matching di riga cerca la chiave tra le celle di TUTTE le tabelle della pagina
(gestisce pagine multi-tabella e header ripetuti). Per le tabelle multilingua (Silca)
ogni lingua usa colonne neutre (ref/codice/quantita/prezzo) + la sua `descrizione_<lang>`.

Uso:
    uv run scripts/evaluate_ingestion_p1.py --engine marker
    uv run scripts/evaluate_ingestion_p1.py --engine baseline
Output: output/spike/<engine>/_metrics.json (+ riepilogo a schermo).
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GT_DIR = ROOT / "data" / "ground_truth"
MARKER_RAW = ROOT / "output" / "spike" / "marker" / "raw"
BASELINE_DIR = ROOT / "output" / "spike" / "baseline"

# table_id -> (file PDF stem, pagina 1-based) per ritrovare l'output baseline per-pagina
PDF_PAGES = {
    "NORDCAP_P003_DE": ("NordCap_Esploso_Ricambi_DE", 3),
    "NORDCAP_P004_DE": ("NordCap_Esploso_Ricambi_DE", 4),
    "SILCA_P003": ("Silca_TriaxPro_Ricambi_Multilingua", 3),
    "SILCA_P004": ("Silca_TriaxPro_Ricambi_Multilingua", 4),
    "MAMOLI_P060_IT": ("Mamoli_Catalogo_Ricambi_2021_IT_EN", 60),
    "MAMOLI_P231_IT": ("Mamoli_Catalogo_Ricambi_2021_IT_EN", 231),
}
LANGS = ["it", "en", "de", "fr", "es"]
MD_ROW = re.compile(r"^\s*\|.*\|\s*$")
SEP_ROW = re.compile(r"^\s*\|[\s:|-]+\|\s*$")


# ----------------------------------------------------------- normalizzazione ---
def norm(v: str, *, is_code: bool = False, numeric: bool = False) -> str:
    s = "" if v is None else str(v)
    s = re.sub(r"[*_`]", "", s)          # via markup markdown (** nei codici!)
    s = s.replace("<br>", " ")
    s = re.sub(r"\s+", " ", s).strip()
    if numeric:
        s = s.replace(".", "").replace(",", ".")  # 1.234,50 -> 1234.50
        s = re.sub(r"[^\d.\-]", "", s)
        return s
    if is_code:
        return re.sub(r"\s+", "", s)     # codici: niente spazi, case-sensitive
    return s.lower()                      # descrizioni: case-insensitive


def jaccard(a: str, b: str) -> float:
    """Similarita' token-set (per descAcc fuzzy: tollera case/spazi/ordine minori)."""
    ta = set(re.findall(r"\w+", a.lower()))
    tb = set(re.findall(r"\w+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


DESC_FUZZY_THRESHOLD = 0.6  # descrizione "corretta" se jaccard >= soglia


# ------------------------------------------------------ lettura output parser ---
def md_tables_rows(md: str) -> list[list[str]]:
    """Tutte le righe-dati di tutte le tabelle markdown della pagina."""
    rows: list[list[str]] = []
    for line in md.splitlines():
        if not MD_ROW.match(line) or SEP_ROW.match(line):
            continue
        rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows


def parser_markdown(engine: str, table_id: str) -> str:
    if engine in ("marker", "docling", "docling_normalized"):
        raw = ROOT / "output" / "spike" / engine / "raw"
        base = raw / table_id
        mds = list(base.rglob("*.md")) if base.exists() else []
        if not mds:
            mds = list(raw.rglob(f"{table_id}*/*.md"))
        return "\n".join(p.read_text(encoding="utf-8") for p in mds)
    if engine == "baseline":
        stem, page = PDF_PAGES[table_id]
        p = BASELINE_DIR / stem / "markdown" / f"{stem}_p{page:03d}.md"
        return p.read_text(encoding="utf-8") if p.exists() else ""
    raise ValueError(engine)


# ---------------------------------------------------------------- ground truth --
def load_gt(table_id: str) -> tuple[list[dict], dict]:
    d = GT_DIR / table_id
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    with (d / "expected.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return rows, meta


def key_col(cols: list[str]) -> str:
    for c in ("codice", "ref", "pos"):
        if c in cols:
            return c
    return cols[0]


def lang_columns(cols: list[str], lang: str) -> list[str]:
    """Colonne rilevanti per una lingua: neutre + descrizione_<lang> (o descrizione)."""
    neutral = [c for c in cols if c in ("pos", "ref", "codice", "quantita", "prezzo")]
    if f"descrizione_{lang}" in cols:
        return neutral + [f"descrizione_{lang}"]
    if "descrizione" in cols:
        return neutral + ["descrizione"]
    return neutral


# ------------------------------------------------------------------- valutazione
def eval_table(engine: str, table_id: str) -> dict:
    gt_rows, meta = load_gt(table_id)
    cols = meta["cols"]
    langs = meta["lang"]
    key = key_col(cols)

    prows = md_tables_rows(parser_markdown(engine, table_id))
    # celle normalizzate come codici (per il match chiave) + testo riga unito
    prows_norm = [[norm(c, is_code=True) for c in r] for r in prows]
    prows_join = [norm(" ".join(r)) for r in prows]  # per il match "loose"
    matched_parser_rows: set[int] = set()

    per_lang: dict[str, dict] = {}
    for lang in (langs if isinstance(langs, list) else [langs]):
        sel = [c for c in lang_columns(cols, lang) if c != key]
        stats = {"gt_rows": 0, "rows_recalled": 0,
                 "cells_total": 0, "cells_strict": 0, "cells_loose": 0,
                 "per_col": {}}
        for gr in gt_rows:
            keyval = norm(gr.get(key, ""), is_code=True)
            if not keyval:
                continue
            stats["gt_rows"] += 1
            # trova la riga parser che contiene la chiave tra le sue celle
            hit = next((i for i, cells in enumerate(prows_norm)
                        if any(keyval == c or (len(keyval) >= 4 and keyval in c)
                               for c in cells)), None)
            recalled = hit is not None
            if recalled:
                stats["rows_recalled"] += 1
                matched_parser_rows.add(hit)
                row_cells = [norm(c) for c in prows[hit]]
                row_text = prows_join[hit]
            for col in sel:
                gval = gr.get(col, "")
                if gval is None or str(gval).strip() == "":
                    continue  # cella GT vuota: non conta
                numeric = col in ("quantita", "prezzo")
                is_desc = col.startswith("descrizione")
                gnorm = norm(gval, numeric=numeric)
                stats["cells_total"] += 1
                mkey = "desc" if is_desc else {"quantita": "qty", "prezzo": "price"}.get(col, col)
                pc = stats["per_col"].setdefault(
                    mkey, {"tot": 0, "strict": 0, "loose": 0, "fuzzy": 0})
                pc["tot"] += 1
                if not recalled or not gnorm:
                    continue
                if numeric:
                    cell_norm = [norm(c, numeric=True) for c in prows[hit]]
                    strict = gnorm in cell_norm
                    loose = strict
                    fz = strict
                elif is_desc:
                    strict = gnorm in row_cells
                    loose = gnorm in row_text
                    fz = loose or jaccard(gnorm, row_text) >= DESC_FUZZY_THRESHOLD
                else:
                    strict = gnorm in row_cells
                    loose = gnorm in row_text
                    fz = loose
                if strict:
                    stats["cells_strict"] += 1
                    pc["strict"] += 1
                if loose:
                    stats["cells_loose"] += 1
                    pc["loose"] += 1
                if fz:
                    pc["fuzzy"] += 1
        per_lang[lang] = stats

    # Structure Fidelity (per tabella): usa la lingua "principale" (row_recall migliore)
    best = max(per_lang.values(), key=lambda s: s["gt_rows"]) if per_lang else None
    n_gt = best["gt_rows"] if best else 0
    recall = (best["rows_recalled"] / n_gt) if n_gt else 0.0
    n_expected_cols = len([c for c in cols if c != key])
    modal_cols = _modal([len(r) for r in prows]) if prows else 0
    mv_ratio = (sum(1 for r in prows for c in r if "<br>" in c) /
                max(sum(len(r) for r in prows), 1))
    if not prows or recall < 0.25:
        fidelity = 0
    elif recall >= 0.90 and modal_cols == n_expected_cols and mv_ratio < 0.05:
        fidelity = 2
    else:
        fidelity = 1

    fp_rows = len([i for i in range(len(prows)) if i not in matched_parser_rows
                   and not _is_sep_or_header(prows[i])])
    return {"table_id": table_id, "langs": list(per_lang.keys()),
            "row_recall": round(recall, 3), "structure_fidelity": fidelity,
            "expected_cols": n_expected_cols, "modal_cols": modal_cols,
            "multivalue_ratio": round(mv_ratio, 3),
            "false_positive_rows": fp_rows, "per_lang": per_lang}


def _modal(xs: list[int]) -> int:
    from collections import Counter
    return Counter(xs).most_common(1)[0][0] if xs else 0


def _is_sep_or_header(cells: list[str]) -> bool:
    joined = " ".join(cells).lower()
    return any(h in joined for h in ("codice", "code", "descriz", "artikel", "description"))


def aggregate(tables: list[dict]) -> dict:
    agg: dict[str, dict] = {}
    for t in tables:
        for lang, s in t["per_lang"].items():
            a = agg.setdefault(lang, {"n_tab": 0, "gt_rows": 0, "rows_recalled": 0,
                                      "cells_total": 0, "cells_strict": 0,
                                      "cells_loose": 0, "fp_rows": 0, "fidelity": [],
                                      "col": {}})
            a["n_tab"] += 1
            a["gt_rows"] += s["gt_rows"]
            a["rows_recalled"] += s["rows_recalled"]
            a["cells_total"] += s["cells_total"]
            a["cells_strict"] += s["cells_strict"]
            a["cells_loose"] += s["cells_loose"]
            a["fidelity"].append(t["structure_fidelity"])
            a["fp_rows"] += t["false_positive_rows"]
            for mkey, pc in s["per_col"].items():
                c = a["col"].setdefault(mkey, {"tot": 0, "strict": 0, "loose": 0, "fuzzy": 0})
                for k in ("tot", "strict", "loose", "fuzzy"):
                    c[k] += pc.get(k, 0)
    out = {}
    for lang, a in agg.items():
        ct = a["cells_total"] or 1
        gr = a["gt_rows"] or 1
        # accuratezza per colonna: desc=fuzzy, qty/price=strict
        def col_acc(mkey: str, kind: str) -> float | None:
            c = a["col"].get(mkey)
            return round(c[kind] / c["tot"], 3) if c and c["tot"] else None
        out[lang] = {
            "n_tabelle": a["n_tab"],
            "codeAcc": round(a["rows_recalled"] / gr, 3),         # = row_recall (codice trovato)
            "descAcc_fuzzy": col_acc("desc", "fuzzy"),
            "descAcc_strict": col_acc("desc", "strict"),
            "qtyAcc": col_acc("qty", "strict"),
            "priceAcc": col_acc("price", "strict"),
            "structure_fidelity_media": round(sum(a["fidelity"]) / len(a["fidelity"]), 2),
            "cell_accuracy_strict_all": round(a["cells_strict"] / ct, 3),
            "false_positive_rows": a["fp_rows"],
            "cells_valutate": a["cells_total"],
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine",
                    choices=["marker", "baseline", "docling", "docling_normalized"],
                    required=True)
    ap.add_argument("--tables", nargs="*", default=list(PDF_PAGES.keys()))
    args = ap.parse_args()

    tables = []
    for tid in args.tables:
        if not (GT_DIR / tid / "expected.csv").exists():
            print(f"[eval] salto {tid}: GT assente")
            continue
        tables.append(eval_table(args.engine, tid))

    per_lang = aggregate(tables)
    out_dir = ROOT / "output" / "spike" / args.engine
    out_dir.mkdir(parents=True, exist_ok=True)
    result = {"engine": args.engine, "per_lingua": per_lang, "per_tabella": tables}
    (out_dir / "_metrics.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[eval] ENGINE = {args.engine}  —  per lingua e per colonna (SPEC §F.1):")
    print(f"{'LANG':4}  {'#tab':>4}  {'codeAcc':>7}  {'descAcc(fz)':>11}  "
          f"{'qtyAcc':>6}  {'priceAcc':>8}  {'structFid':>9}  {'fpRows':>6}")

    def fmt(x):
        return "  -  " if x is None else f"{x:.3f}"
    for lang in LANGS:
        if lang not in per_lang:
            continue
        m = per_lang[lang]
        print(f"{lang:4}  {m['n_tabelle']:>4}  {fmt(m['codeAcc']):>7}  "
              f"{fmt(m['descAcc_fuzzy']):>11}  {fmt(m['qtyAcc']):>6}  "
              f"{fmt(m['priceAcc']):>8}  {m['structure_fidelity_media']:>9}  "
              f"{m['false_positive_rows']:>6}")
    print(f"\n[eval] per-tabella e dettaglio in {out_dir}/_metrics.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
