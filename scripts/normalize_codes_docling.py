# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""P1.5+ — Normalizzatore DETERMINISTICO dei codici sopra l'output Docling.

Motivazione: Docling (do_ocr=False) legge i codici Mamoli dal testo nativo ma li
rende spezzati da spazi/markup ('V0 91568 00007') e a volte fonde due codici in una
cella ('V0 91920 00001 V0 91925 00001'). Il codice canonico esatto serve al guardrail
P3. Qui lo ricomponiamo con regole deterministiche, conservative, AUDITABILI.
Nessun LLM, nessuna euristica opaca: ogni trasformazione e' classificata e loggata.

Regole (vedi _FINDINGS_normalized.md):
  A cleanup:  trim, rimozione markup (* _ `), <br>/tag, caratteri invisibili.
  B rejoin:   famiglia-codice lunga Mamoli 'V0'+10 alnum -> uniti (safe_rejoin);
              findall separa i codici doppi fusi in una cella.
  C prefisso: token puramente numerico lungo ~10-11 compatibile con 'V0...' ->
              NON applicato d'ufficio: classificato unsafe_candidate e solo loggato.
  D safe mode: solo le trasformazioni safe_* entrano nell'output; unsafe solo log.
  E logging:  per ogni codice: originale, normalizzato, tipo, motivo.

Input : output/spike/docling/raw/<table_id>/<table_id>.md (gia' generato in P1.5)
Output: output/spike/docling_normalized/
          raw/<table_id>/<table_id>.md     (markdown con codici canonici, per l'eval)
          <table_id>/codes_normalized.csv  (codici canonici + classe + motivo)
          _normalization_log.json          (log completo + flag tabelle fuse)
          _code_accuracy.json              (codeAcc raw vs normalized vs GT, per tabella)
Uso: uv run scripts/normalize_codes_docling.py
"""

from __future__ import annotations

import csv
import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCLING_RAW = ROOT / "output" / "spike" / "docling" / "raw"
OUT = ROOT / "output" / "spike" / "docling_normalized"
GT = ROOT / "data" / "ground_truth"

TABLES = ["MAMOLI_P231_IT", "MAMOLI_P060_IT"]

# Famiglia-codice lunga Mamoli: 'V0' + 10 caratteri alfanumerici (maiuscoli/cifre),
# con spazi/markup interni tollerati. findall separa i codici doppi in una cella.
LONG_CODE = re.compile(r"V0(?:[\s*_`]*[0-9A-Z]){10}")
# Famiglia corta (p060): token alfanumerico 2-8 char, gia' pulito nel nativo.
# Ammette . - / (es. codici 'N.D.', '2500-A-VH-S', '0100').
SHORT_CODE = re.compile(r"^[0-9A-Z][0-9A-Z./-]{1,9}$")
INVISIBLE = re.compile(r"[­​-‏  ﻿]")
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


def gt_codes(table_id: str) -> set[str]:
    p = GT / table_id / "expected.csv"
    return {canonical(r["codice"]) for r in csv.DictReader(p.open(encoding="utf-8"))
            if r.get("codice", "").strip()}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    full_log: dict = {}
    acc: dict = {}

    for tid in TABLES:
        md_path = DOCLING_RAW / tid / f"{tid}.md"
        if not md_path.exists():
            print(f"[norm] salto {tid}: {md_path} assente")
            continue
        md = md_path.read_text(encoding="utf-8")
        rows = cells_rows(md)
        fused = detect_fused(rows)

        # raw exact: cella (solo cleanup, SENZA rejoin) == codice GT
        gtc = gt_codes(tid)
        raw_exact = set()
        norm_codes: set[str] = set()
        entries: list[dict] = []
        norm_rows: list[list[str]] = []
        for r in rows:
            new_row = []
            for c in r:
                clean = base_cleanup(c)
                if canonical(clean) in gtc and " " not in clean.strip():
                    raw_exact.add(canonical(clean))
                codes, logs = normalize_cell(c)
                entries.extend(logs)
                for cc in codes:
                    norm_codes.add(canonical(cc))  # match sempre sulla forma canonica
                # cella normalizzata per l'eval: se contiene codici, metti i canonici
                new_row.append(" ".join(codes) if codes else clean)
            norm_rows.append(new_row)

        # scrivi output normalizzato
        d = OUT / tid
        d.mkdir(parents=True, exist_ok=True)
        raw_d = OUT / "raw" / tid
        raw_d.mkdir(parents=True, exist_ok=True)
        md_lines = ["| " + " | ".join(r) + " |" for r in norm_rows]
        (raw_d / f"{tid}.md").write_text("\n".join(md_lines), encoding="utf-8")
        with (d / "codes_normalized.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["original", "normalized", "type", "reason"])
            w.writeheader()
            w.writerows([e for e in entries if e["normalized"]])

        raw_hit = len(gtc & raw_exact)
        norm_hit = len(gtc & norm_codes)
        acc[tid] = {
            "gt_codes": len(gtc),
            "codeAcc_raw_exact": round(raw_hit / len(gtc), 3),
            "codeAcc_normalized_exact": round(norm_hit / len(gtc), 3),
            "recovered": sorted(gtc & norm_codes - raw_exact),
            "still_missing": sorted(gtc - norm_codes),
            "unsafe_candidates": [e for e in entries if e["type"] == "unsafe_candidate"],
            "fused_table": fused,
        }
        full_log[tid] = entries
        print(f"[norm] {tid}: GT={len(gtc)}  raw_exact={acc[tid]['codeAcc_raw_exact']}  "
              f"normalized_exact={acc[tid]['codeAcc_normalized_exact']}  "
              f"fused={fused['fused_suspected']}  ancora_mancanti={len(acc[tid]['still_missing'])}")

    (OUT / "_normalization_log.json").write_text(
        json.dumps(full_log, indent=2, ensure_ascii=False), encoding="utf-8")
    (OUT / "_code_accuracy.json").write_text(
        json.dumps(acc, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[norm] OK — log e accuratezza in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
