# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""P2.2 — CLI del chunking difensivo sui markdown normalizzati (P1.5+).

Per ogni tabella produce:
  output/spike/chunks/<table_id>/chunks.jsonl        un chunk = un record logico
  output/spike/chunks/<table_id>/_rejects.jsonl      corsia di scarto esplicita
  output/spike/chunks/_chunking_report.json          report + riconciliazione

CONTRATTO DI COPERTURA (fail-fast, principio del guardrail P3 anticipato):
  1. Nessun codice inventato: ogni codice nei chunk deve esistere in
     codes_normalized.csv (l'audit trail di P1).
  2. Copertura spiegata: ogni valore normalizzato del CSV deve ricomparire nei
     chunk (come codice, pos o quantita' — il CSV di P1 classificava come "codice
     corto" anche pos/qty a token singolo) oppure in un reject motivato.
Violazione di uno dei due -> exit code 1: niente sparizioni silenziose.

Uso: uv run scripts/chunk_tables.py
Exit: 0 = chunking completato e contratto rispettato; 1 = violazione.
"""

from __future__ import annotations

import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.ingestion.chunking import chunk_table  # noqa: E402

NORM = ROOT / "output" / "spike" / "docling_normalized"
OUT = ROOT / "output" / "spike" / "chunks"

# Metadati di citazione per table_id (per lo spike; a regime dal catalogo corpus).
TABLE_META = {
    "MAMOLI_P231_IT": {"doc": "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf",
                       "page": 231, "lang": "IT"},
    "MAMOLI_P060_IT": {"doc": "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf",
                       "page": 60, "lang": "IT"},
}


def load_norm_map(table_id: str) -> dict[str, dict]:
    """normalized -> {original, type} dal CSV di audit P1 (prima occorrenza vince)."""
    path = NORM / table_id / "codes_normalized.csv"
    norm_map: dict[str, dict] = {}
    for row in csv.DictReader(path.open(encoding="utf-8")):
        norm_map.setdefault(row["normalized"], {"original": row["original"],
                                                "type": row["type"]})
    return norm_map


def reconcile(norm_map: dict, chunks: list, rejects: list) -> dict:
    """Riconciliazione bidirezionale chunker <-> audit CSV di P1."""
    csv_values = set(norm_map)
    chunk_codes = {c.code for c in chunks}
    invented = sorted(chunk_codes - csv_values)

    accounted_in_chunks = set()
    for c in chunks:
        accounted_in_chunks.update(v for v in (c.code, c.pos, c.qty) if v)
        # token delle celle sorgente: un valore del CSV che sta in una riga
        # diventata chunk e' tracciato anche se il campo e' stato degradato
        # (es. prezzo senza virgola '2150' -> price=None + warning, non sparito).
        for cell in c.source_cells:
            accounted_in_chunks.update(cell.split())
    reject_tokens = set()
    for r in rejects:
        reject_tokens.update(r.codes_involved)
        for cell in r.source_cells:
            reject_tokens.update(cell.split())
    unaccounted = sorted(csv_values - accounted_in_chunks - reject_tokens)
    return {"csv_values": len(csv_values),
            "invented_codes": invented,
            "unaccounted_values": unaccounted,
            "ok": not invented and not unaccounted}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    full_report: dict = {}
    contract_ok = True

    for tid, meta in TABLE_META.items():
        md_path = NORM / "raw" / tid / f"{tid}.md"
        if not md_path.exists():
            print(f"[chunk] FATALE: input assente {md_path} "
                  f"(rigenerare con normalize_codes_docling.py)")
            return 1
        norm_map = load_norm_map(tid)
        res = chunk_table(tid, md_path.read_text(encoding="utf-8"), meta, norm_map)
        chunks, rejects, report = res["chunks"], res["rejects"], res["report"]

        d = OUT / tid
        d.mkdir(parents=True, exist_ok=True)
        with (d / "chunks.jsonl").open("w", encoding="utf-8") as fh:
            for c in chunks:
                fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
        with (d / "_rejects.jsonl").open("w", encoding="utf-8") as fh:
            for r in rejects:
                fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")

        rec = reconcile(norm_map, chunks, rejects)
        report["reconciliation"] = rec
        full_report[tid] = report
        contract_ok &= rec["ok"]

        ch = report["chunks"]
        print(f"[chunk] {tid}: blocchi={report['blocks']} chunks={ch['total']} "
              f"(ok={ch['ok']} recovered={ch['recovered_multirow']} "
              f"degraded={ch['degraded']}) rejects={report['rejects'] or '{}'}")
        print(f"        riconciliazione CSV: valori={rec['csv_values']} "
              f"inventati={rec['invented_codes'] or 'nessuno'} "
              f"non-spiegati={rec['unaccounted_values'] or 'nessuno'}")

    (OUT / "_chunking_report.json").write_text(
        json.dumps(full_report, indent=2, ensure_ascii=False), encoding="utf-8")
    if not contract_ok:
        print("\n[chunk] CONTRATTO DI COPERTURA VIOLATO — vedi report. Exit 1.")
        return 1
    print(f"\n[chunk] OK — contratto rispettato. Output in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
