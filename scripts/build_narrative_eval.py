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

Changelog (righello — mai in silenzio)
---------------------------------------
- **2026-08-24, v1.1** (`data/eval/eval_narrative_v1_1.jsonl`, NON sostituisce
  `eval_narrative_v1.jsonl` che resta storico e immutato — i numeri passati
  come 12/56, 18/56, R@10 0.625 restano leggibili SOLO contro quel file):
  1. **Dedup query duplicate**: 7 query compaiono sia in `eval_set_v0.jsonl`
     (Q0xx) sia come riga "carryover-v0" in `eval_set_v1.jsonl` (C0xx), stesso
     testo esatto. Prima venivano promosse ENTRAMBE, contando la stessa
     domanda due volte. Ora vince l'ultima occorrenza nell'ordine di SOURCES
     (v1 supera v0): le note d'autore ("expected ALLARGATO") dicono
     esplicitamente che la riga v1 e' la correzione.
  2. **Caso rovesciato riparato**: "aeratore 24x1 di ricambio" — Q035 (v0,
     `expected` stretto) restava nel set kept mentre C007 (v1, `expected`
     ALLARGATO, la correzione) finiva in `_excluded` per collisione di codice
     (0448/11188 esistono anche in un altro documento). Con la precedenza v1,
     ora e' C007 l'unica riga tentata: risulta non risolvibile in modo sicuro
     e la query esce interamente dal set kept — invece di restarci con un
     target stale.
  3. **Gemelli descrittivi**: per ogni item risolto via codice e con un solo
     documento target, lo script cerca — nello stesso documento, per
     `desc_search` (descrizione normalizzata) identica a quella dei chunk
     attesi — altri codici con la STESSA descrizione ma fuori dai target
     attuali. Sono annotati nei campi `descriptive_twins_outside_target` /
     `n_descriptive_twins`, **senza modificare `targets`**: allargare i
     bersagli automaticamente e' stato giudicato rischioso (item con decine
     di gemelli su cataloghi grandi, es. "movimento ceramico" in Mamoli), la
     decisione se e come usarli e' lasciata a chi legge il report.

Uso:  .venv/bin/python scripts/build_narrative_eval.py [--out PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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
DATASET_VERSION = "v1.1"
PREVIOUS_OUT = ROOT / "data" / "eval" / "eval_narrative_v1.jsonl"
OUT = ROOT / "data" / "eval" / "eval_narrative_v1_1.jsonl"
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


def _twins_for_doc(client, doc: str, desc_search_values: set[str],
                    expected_codes: set[str],
                    target_pairs: set[tuple[str, int]]) -> list[dict]:
    """Righe indicizzate nello stesso `doc` con `desc_search` identica a una
    di quelle attese, codice diverso da quelli attesi, pagina fuori dai
    target attuali. Query live (una per descrizione distinta); vedi
    `_find_twins` per la logica pura, testata senza DB."""
    catalog = []
    for desc in desc_search_values:
        flt = models.Filter(must=[
            models.FieldCondition(key="doc", match=models.MatchValue(value=doc)),
            models.FieldCondition(key="desc_search", match=models.MatchValue(value=desc)),
        ])
        for p in _scroll(client, flt):
            catalog.append(p.payload)
    return _find_twins(catalog, expected_codes, target_pairs)


def _find_twins(catalog: list[dict], expected_codes: set[str],
                 target_pairs: set[tuple[str, int]]) -> list[dict]:
    """Logica pura (no I/O): dato un catalogo di payload (righe indicizzate
    gia' filtrate su doc+desc_search), quali sono gemelli descrittivi fuori
    dai bersagli attuali. Deterministico e riproducibile su dati fissi."""
    seen = set()
    twins = []
    for pl in catalog:
        code, doc, page = pl.get("code"), pl.get("doc"), pl.get("page")
        if code is None or doc is None or page is None:
            continue
        if code in expected_codes or (doc, page) in target_pairs:
            continue
        key = (doc, page, code)
        if key in seen:
            continue
        seen.add(key)
        twins.append({"doc": doc, "page": page, "code": code})
    return sorted(twins, key=lambda t: (t["doc"], t["page"], t["code"]))


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


def _load_rows() -> list[tuple[str, dict]]:
    """Tutte le righe `query_type == "natural"` dalle SOURCES, in ordine di
    file e di riga. Non fa dedup: quello e' compito di `_dedup`."""
    rows = []
    for src, path in SOURCES:
        for line in path.open(encoding="utf-8"):
            row = json.loads(line)
            if row.get("query_type") != "natural":
                continue
            rows.append((src, row))
    return rows


def _dedup(rows: list[tuple[str, dict]]) -> tuple[list[tuple[str, dict]], list[dict]]:
    """Collassa le query duplicate: stesso testo esatto (`.strip()`).

    Vince l'ULTIMA occorrenza nell'ordine di `rows` (che rispetta l'ordine di
    SOURCES: eval_set_v0 poi eval_set_v1) — quindi v1 supera v0. Le righe
    "carryover-v0" in v1 sono correzioni dichiarate dall'autore (es. "expected
    ALLARGATO"), non varianti indipendenti.

    Logica pura, nessun I/O: testabile senza Qdrant.
    """
    by_query: dict[str, tuple[str, dict]] = {}
    order: list[str] = []
    dedup_log: list[dict] = []
    for src, row in rows:
        q = row["query"].strip()
        if q not in by_query:
            order.append(q)
        else:
            prev_src, prev_row = by_query[q]
            dedup_log.append({
                "query": q,
                "kept": {"set": src, "qa_id": row["qa_id"]},
                "dropped": {"set": prev_src, "qa_id": prev_row["qa_id"]},
                "reason": "query duplicata (stesso testo esatto in due fonti); "
                          "vince l'ultima nell'ordine SOURCES (v1 supera v0)",
            })
        by_query[q] = (src, row)
    deduped = [by_query[q] for q in order]
    return deduped, dedup_log


def build() -> tuple[list[dict], list[dict], list[dict]]:
    client = get_qdrant_client()
    golden = _golden_targets()
    kept, dropped = [], []

    deduped_rows, dedup_log = _dedup(_load_rows())

    for src, row in deduped_rows:
        pages: dict[tuple[str, int], list[str]] = {}
        descs_by_doc: dict[str, set[str]] = {}
        phantom_hits = 0
        for crit in row["expected"]:
            for pl in _resolve(client, crit):
                if pl.get("doc") == PHANTOM_DOC:
                    phantom_hits += 1
                    continue
                key = (pl.get("doc"), int(pl.get("page")))
                pages.setdefault(key, []).append(pl.get("chunk_id"))
                if pl.get("desc_search"):
                    descs_by_doc.setdefault(pl.get("doc"), set()).add(pl["desc_search"])

        item = {
            "item_id": f"NARR-{row['qa_id']}",
            "query": row["query"],
            "lang": row["lang"],
            "role": ROLE,
            "dataset_version": DATASET_VERSION,
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

        # Gemelli descrittivi: solo per item a documento unico e con expected
        # basato su codice (non doc+pos) — sono informativi, NON toccano
        # `targets`. Vedi changelog nel docstring del modulo.
        expected_codes = {c.get("code") for c in row["expected"] if "code" in c}
        if len(docs_all) == 1 and expected_codes and descs_by_doc.get(docs_all[0]):
            doc = docs_all[0]
            target_pairs = {(t["doc"], t["page"]) for t in targets}
            twins = _twins_for_doc(client, doc, descs_by_doc[doc], expected_codes,
                                    target_pairs)
            item["descriptive_twins_outside_target"] = twins
            item["n_descriptive_twins"] = len(twins)
        else:
            item["descriptive_twins_outside_target"] = []
            item["n_descriptive_twins"] = 0

        kept.append(item)
    return kept, dropped, dedup_log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()

    kept, dropped, dedup_log = build()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for it in kept:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    exc = out.with_name(out.stem + "_excluded.jsonl")
    with exc.open("w", encoding="utf-8") as fh:
        for it in dropped:
            fh.write(json.dumps(it, ensure_ascii=False) + "\n")
    dedup_path = out.with_name(out.stem + "_dedup_log.json")
    dedup_path.write_text(json.dumps(dedup_log, ensure_ascii=False, indent=2),
                           encoding="utf-8")

    manifest = {
        "dataset_version": DATASET_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "out": str(out),
        "n_kept": len(kept),
        "n_excluded": len(dropped),
        "n_dedup_dropped": len(dedup_log),
        "previous_dataset": {
            "path": str(PREVIOUS_OUT),
            "status": "storico, NON rigenerato/toccato da questa versione dello "
                      "script; i numeri misurati su di esso (es. 12/56, 18/56, "
                      "R@10 0.625) vanno letti SOLO contro quel file (56 item "
                      "kept), non contro questo",
        },
        "changes_from_v1": {
            "dedup_query_duplicate": (
                f"{len(dedup_log)} coppie di righe con testo query identico "
                "collassate; vince la riga piu' recente nell'ordine SOURCES "
                "(eval_set_v1 supera eval_set_v0)"),
            "reversed_case_fixed": (
                "'aeratore 24x1 di ricambio': prima Q035 (v0, expected stretto) "
                "restava nel set kept mentre C007 (v1, expected ALLARGATO, la "
                "correzione) finiva in _excluded per collisione di codice; ora "
                "vince C007 e, risultando non risolvibile in modo sicuro, la "
                "query esce dal set kept invece di restarci con un target stale"),
            "descriptive_twins": (
                "aggiunto (solo informativo, non tocca `targets`) il campo "
                "descriptive_twins_outside_target/n_descriptive_twins per gli "
                "item a documento unico risolti via codice"),
        },
        "warning": "Il denominatore e' cambiato: ogni percentuale storica va "
                   "riletta contro il file su cui e' stata misurata.",
    }
    manifest_path = out.with_name(out.stem + "_manifest.json")
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2),
                              encoding="utf-8")

    from collections import Counter
    print(f"scritti  {len(kept)} item -> {out}")
    print(f"scartati {len(dropped)} item -> {exc}")
    print(f"dedup    {len(dedup_log)} coppie collassate -> {dedup_path}")
    print(f"manifest -> {manifest_path}")
    print("per lingua:", dict(Counter(i["lang"] for i in kept)))
    print("focused (<=%d pagine target): %d/%d"
          % (FOCUSED_MAX_PAGES, sum(1 for i in kept if i["focused"]), len(kept)))
    n_twins = sum(1 for i in kept if i["n_descriptive_twins"] > 0)
    print(f"item con >=1 gemello descrittivo fuori bersaglio: {n_twins}/{len(kept)}")
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
