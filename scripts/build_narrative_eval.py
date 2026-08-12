"""Costruisce il METRO NARRATIVO e2e (`data/eval/eval_narrative_v1.jsonl`).

Perche' esiste
--------------
Il golden gate ha **3 sole query narrative** end-to-end, tutte italiane, su 119
documenti indicizzati: il ramo piu' debole del sistema e' anche l'unico che
nessuno puo' vedere. EN e DE hanno copertura zero. Qui si costruisce un set
narrativo permanente, **senza LLM e senza giudizio soggettivo**, promuovendo a
item e2e le query gia' esistenti.

Metodo (deterministico, ripetibile, auditabile)
-----------------------------------------------
1. Sorgenti: `data/eval/eval_set_v0.jsonl` e `eval_set_v1.jsonl`, righe con
   `query_type == "natural"`. Sono state scritte a giugno/luglio, PRIMA di
   qualunque cambiamento oggi in esame: non sono state scelte per far passare
   un test.
2. Ogni riga porta un `expected` in forma di CODICE (o `doc`+`pos`). Il codice
   viene risolto a `(doc, pagina)` **interrogando l'indice** (`aike_chunks`,
   filtro esatto su `payload.code`): il target non lo decide una persona, lo
   decide il dato indicizzato.
3. Filtri di onesta' (ognuno toglie item, mai ne aggiunge):
   - **doc-stem fantasma**: `Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf` (176 chunk,
     sole pagine 60 e 231, testo corrotto tipo "d eviatore /d iverter") convive
     con `Mamoli_Catalogo_Ricambi_2021_IT_EN` (7249 chunk, integro). Le
     risoluzioni al fantasma sono target SBAGLIATI e vengono buttate; gli item
     che risolvono SOLO li' escono dal set.
   - **codici ambigui tra documenti**: lo stesso codice puo' esistere in
     cataloghi di marchi diversi indicando prodotti diversi (verificato:
     `438304` = "VITE M5x30 UNI 7688 ZINC NERO" in manzoni_comac_spareparts_B
     e "a richiesta" in valvorobica_flange_raccordi_listino_2025). Un item che
     risolve a piu' DOCUMENTI non ha un target attendibile: esce dal set
     principale (resta nel file `_excluded` con il motivo). Unica eccezione:
     gli item per cui `data/golden/golden_v1.jsonl` porta gia' un target
     curato — li' la disambiguazione esiste, e' versionata e verificabile.
4. Ogni item dichiara provenienza (set + qa_id originale) e derivazione: chi
   legge deve poter rifare il ragionamento senza fidarsi di questo script.

Cosa NON garantisce
-------------------
Che la query sia davvero risposta dal chunk atteso: quello lo dice solo la
lettura umana. Per questo lo script stampa un CAMPIONE da verificare a mano e
il report deve riportarne l'esito.

Uso:  .venv/bin/python scripts/build_narrative_eval.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.run_config import load_runtime_env  # noqa: E402

load_runtime_env()

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from qdrant_client.http import models  # noqa: E402

COLLECTION = "aike_chunks"
PHANTOM_DOC = "Mamoli_Catalogo_Ricambi_2021_IT_EN.pdf"
SOURCES = [("eval_set_v0", ROOT / "data" / "eval" / "eval_set_v0.jsonl"),
           ("eval_set_v1", ROOT / "data" / "eval" / "eval_set_v1.jsonl")]
GOLDEN = ROOT / "data" / "golden" / "golden_v1.jsonl"
OUT = ROOT / "data" / "eval" / "eval_narrative_v1.jsonl"
ROLE = "tecnico"
FOCUSED_MAX_PAGES = 3


def _scroll(client, flt: models.Filter) -> list:
    out, offset = [], None
    while True:
        pts, offset = client.scroll(COLLECTION, scroll_filter=flt, limit=512,
                                    with_payload=True, with_vectors=False,
                                    offset=offset)
        out += pts
        if offset is None:
            return out


def _resolve(client, crit: dict) -> list[dict]:
    """Un criterio `expected` -> i chunk indicizzati che lo soddisfano."""
    if "code" in crit:
        flt = models.Filter(must=[models.FieldCondition(
            key="code", match=models.MatchValue(value=crit["code"]))])
    else:
        flt = models.Filter(must=[
            models.FieldCondition(key="doc",
                                  match=models.MatchValue(value=crit["doc"])),
            models.FieldCondition(key="pos",
                                  match=models.MatchValue(value=str(crit["pos"]))),
        ])
    return [p.payload for p in _scroll(client, flt)]


def _golden_targets() -> dict[str, dict]:
    """query normalizzata -> target curato del golden gate (se esiste)."""
    out = {}
    if not GOLDEN.exists():
        return out
    for line in GOLDEN.open(encoding="utf-8"):
        row = json.loads(line)
        if row.get("intent") == "narrative" and row.get("expected"):
            out[row["query"].strip().lower()] = {
                "qa_id": row["qa_id"], "doc": row["expected"]["doc"],
                "page": int(row["expected"]["page"])}
    return out


def build() -> tuple[list[dict], list[dict]]:
    client = get_qdrant_client()
    golden = _golden_targets()
    kept, dropped = [], []

    for src, path in SOURCES:
        for line in path.open(encoding="utf-8"):
            row = json.loads(line)
            if row.get("query_type") != "natural":
                continue
            pages: dict[tuple[str, int], list[str]] = {}
            phantom_hits = 0
            for crit in row["expected"]:
                for pl in _resolve(client, crit):
                    if pl.get("doc") == PHANTOM_DOC:
                        phantom_hits += 1
                        continue
                    key = (pl.get("doc"), int(pl.get("page")))
                    pages.setdefault(key, []).append(pl.get("chunk_id"))

            item = {
                "item_id": f"NARR-{row['qa_id']}",
                "query": row["query"],
                "lang": row["lang"],
                "role": ROLE,
                "provenance": {"set": src, "qa_id": row["qa_id"],
                               "expected": row["expected"],
                               "note": row.get("note")},
            }
            docs = sorted({d for d, _ in pages})

            if not pages:
                item["excluded_reason"] = (
                    f"nessun target reale: {phantom_hits} chunk risolti solo sul "
                    f"doc-stem fantasma {PHANTOM_DOC}" if phantom_hits else
                    "nessun chunk indicizzato per l'expected")
                dropped.append(item)
                continue

            gold = golden.get(row["query"].strip().lower())
            if len(docs) > 1 and gold:
                targets = [{"doc": gold["doc"], "page": gold["page"]}]
                derivation = (
                    f"codice/i {[c.get('code') for c in row['expected']]} risolti "
                    f"sull'indice a {len(pages)} (doc,pagina) in {len(docs)} documenti "
                    f"(collisione di codice tra cataloghi); target DISAMBIGUATO con "
                    f"data/golden/golden_v1.jsonl {gold['qa_id']}")
                docs_all = docs
            elif len(docs) > 1:
                item["excluded_reason"] = (
                    f"codice ambiguo: risolve a {len(docs)} documenti diversi "
                    f"({docs}) e nessun target curato nel golden set")
                item["resolved"] = [{"doc": d, "page": p} for d, p in sorted(pages)]
                dropped.append(item)
                continue
            else:
                targets = [{"doc": d, "page": p} for d, p in sorted(pages)]
                derivation = (
                    f"codice/i {[c.get('code') for c in row['expected']]} -> "
                    f"aike_chunks payload.code -> {len(targets)} (doc,pagina), "
                    f"documento unico" +
                    (f"; {phantom_hits} chunk sul doc-stem fantasma scartati"
                     if phantom_hits else ""))
                docs_all = docs

            item.update({
                "targets": targets,
                "target_docs": docs_all,
                "n_target_pages": len(targets),
                "focused": len(targets) <= FOCUSED_MAX_PAGES,
                "expected_chunk_ids": sorted(
                    cid for cids in pages.values() for cid in cids if cid)[:20],
                "phantom_chunks_discarded": phantom_hits,
                "derivation": derivation,
            })
            kept.append(item)
    return kept, dropped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    kept, dropped = build()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for it in kept:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    exc = out.with_name(out.stem + "_excluded.jsonl")
    with exc.open("w", encoding="utf-8") as fh:
        for it in dropped:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")

    from collections import Counter
    print(f"scritti  {len(kept)} item -> {out}")
    print(f"scartati {len(dropped)} item -> {exc}")
    print("per lingua:", dict(Counter(i["lang"] for i in kept)))
    print("focused (<=%d pagine target): %d/%d"
          % (FOCUSED_MAX_PAGES, sum(1 for i in kept if i["focused"]), len(kept)))
    print("motivi di scarto:")
    for it in dropped:
        print(f"  {it['item_id']:<12} {it['excluded_reason']}")
    print("\nCAMPIONE da verificare a mano (apri i chunk attesi e giudica):")
    for it in kept[:: max(1, len(kept) // 9)][:9]:
        print(f"  {it['item_id']:<12} [{it['lang']}] {it['query']!r} -> "
              f"{it['targets'][:2]}{' ...' if it['n_target_pages'] > 2 else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
