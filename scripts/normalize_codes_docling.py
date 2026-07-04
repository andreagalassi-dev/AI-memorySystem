"""P1.5+ — Normalizzatore DETERMINISTICO dei codici sopra l'output Docling.

Motivazione: Docling (do_ocr=False) legge i codici Mamoli dal testo nativo ma li
rende spezzati da spazi/markup ('V0 91568 00007') e a volte fonde due codici in una
cella ('V0 91920 00001 V0 91925 00001'). Il codice canonico esatto serve al guardrail
P3. Qui lo ricomponiamo con regole deterministiche, conservative, AUDITABILI.
Nessun LLM, nessuna euristica opaca: ogni trasformazione e' classificata e loggata.

Debito §1: lo script e' BRAND-AGNOSTICO — il profilo di normalizzazione arriva
dalle tabelle Postgres dei Profili di Ingestion via brands.resolve(table_id)
(doc_map a glob), non piu' hardcoded su Mamoli. Richiede il Postgres del
compose seedato (scripts/seed_ingestion_profiles.py); fail-closed se giu'.

Regole (vedi _FINDINGS_normalized.md; oggi definite nel profilo a DB):
  A cleanup:  trim, rimozione markup (* _ `), <br>/tag, caratteri invisibili.
  B rejoin:   famiglia-codice lunga (es. Mamoli 'V0'+10 alnum) -> uniti
              (safe_rejoin); findall separa i codici doppi fusi in una cella.
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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingestion.brands import normalize_cell, resolve  # noqa: E402
from src.ingestion.codes import (  # noqa: E402
    base_cleanup,
    canonical,
    cells_rows,
    detect_fused,
)

DOCLING_RAW = ROOT / "output" / "spike" / "docling" / "raw"
OUT = ROOT / "output" / "spike" / "docling_normalized"
GT = ROOT / "data" / "ground_truth"

TABLES = ["MAMOLI_P231_IT", "MAMOLI_P060_IT"]


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
        profile = resolve(tid)  # doc_map a DB: MAMOLI_* -> mamoli

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
                codes, logs = normalize_cell(c, profile)
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
