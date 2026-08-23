"""Controllo ECONOMICO del ramo `code` fra due collezioni, senza girare il gate.

Il ramo `code` e' l'unico saturo del prodotto (22/22 nel golden gate): qualunque
cambiamento su cosa entra nell'indice deve dimostrare di non averlo intaccato,
e deve dimostrarlo PRIMA di spendere un'ora di gate con l'LLM.

Cosa misura, e perche' basta: il gate su una query `code` puo' rispondere bene
solo se il retrieval mette in cima il chunk che porta ESATTAMENTE il codice
atteso. Questo script si ferma li' — stessa route, stessa strategia, stesso
`search()` della produzione, zero LLM, ~1 s per query:

  rank_code  = prima posizione in cui compare un chunk con `payload['code']`
               uguale al codice atteso (confronto esatto, non sottostringa)
  rank_pag   = prima posizione con (doc, pagina) attesi
  quasi      = quanti chunk in cima portano un codice DIVERSO ma simile a
               quello atteso (stessa forma incollata a meno di un carattere):
               e' la classe "quasi giusti" che il progetto vieta, e la ragione
               per cui il codice e' diventato un token atomico

Uso:
  .venv/bin/python scripts/ab_code_branch.py aike_chunks aike_chunks_v2
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.run_config import load_runtime_env  # noqa: E402

load_runtime_env()

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.common.roles import Role  # noqa: E402

GOLDEN = ROOT / "data" / "golden" / "golden_v1.jsonl"
LIMIT = 10


def _glue(s: str) -> str:
    return re.sub(r"[^0-9a-z]", "", (s or "").lower())


def _simile(a: str, b: str) -> bool:
    """'quasi giusto': stessa lunghezza incollata e al piu' un carattere di
    differenza, oppure uno e' prefisso dell'altro con >=4 caratteri comuni."""
    ga, gb = _glue(a), _glue(b)
    if not ga or not gb or ga == gb:
        return False
    if len(ga) == len(gb):
        return sum(1 for x, y in zip(ga, gb) if x != y) <= 1
    corto, lungo = (ga, gb) if len(ga) < len(gb) else (gb, ga)
    return len(corto) >= 4 and lungo.startswith(corto)


def misura(collezione: str, items: list[dict]) -> list[dict]:
    import src.retrieval.bom as bom
    import src.retrieval.search as S

    for m in (S, bom):
        m.COLLECTION = collezione
    assert S.COLLECTION == collezione
    qc = get_qdrant_client()
    righe = []
    for it in items:
        atteso = it["expected"]
        hits = S.search(qc, it["query"], "routed", limit=LIMIT,
                        query_filter=S.access_filter("current", Role(it["role"])))
        codes = [(h.payload or {}).get("code") for h in hits]
        rank_code = next((i + 1 for i, c in enumerate(codes)
                          if c and _glue(c) == _glue(atteso["code"])), None)
        rank_pag = next((i + 1 for i, h in enumerate(hits)
                         if (h.payload or {}).get("doc") == atteso["doc"]
                         and (h.payload or {}).get("page") == atteso["page"]), None)
        quasi = sum(1 for c in codes if _simile(c, atteso["code"]))
        righe.append({"qa_id": it["qa_id"], "query": it["query"],
                      "code": atteso["code"], "rank_code": rank_code,
                      "rank_pag": rank_pag, "quasi": quasi,
                      "top3": codes[:3]})
    return righe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("collezioni", nargs=2, metavar=("A", "B"))
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    items = [json.loads(l) for l in GOLDEN.open(encoding="utf-8")]
    items = [i for i in items if i["intent"] == "code"]
    print(f"query 'code' del golden set: {len(items)}\n")

    a, b = args.collezioni
    ra = {r["qa_id"]: r for r in misura(a, items)}
    rb = {r["qa_id"]: r for r in misura(b, items)}

    def top1(r):
        return sum(1 for x in r.values() if x["rank_code"] == 1)

    def top10(r):
        return sum(1 for x in r.values() if x["rank_code"])

    print(f"{'qa_id':<7} {'codice atteso':<18} {'rank ' + a[:14]:>20} "
          f"{'rank ' + b[:14]:>20}  esito")
    print("-" * 84)
    peggiorati, migliorati = [], []
    for qa in ra:
        x, y = ra[qa], rb[qa]
        rx, ry = x["rank_code"], y["rank_code"]
        if rx == ry:
            esito = "="
        elif ry is None or (rx is not None and ry > rx):
            esito = "PEGGIORA"
            peggiorati.append(qa)
        else:
            esito = "migliora"
            migliorati.append(qa)
        print(f"{qa:<7} {x['code']:<18} {str(rx):>20} {str(ry):>20}  {esito}")

    print("-" * 84)
    print(f"codice esatto a RANGO 1 : {a} {top1(ra)}/{len(items)}   "
          f"|   {b} {top1(rb)}/{len(items)}")
    print(f"codice esatto nei top{LIMIT}: {a} {top10(ra)}/{len(items)}   "
          f"|   {b} {top10(rb)}/{len(items)}")
    print(f"pagina attesa a rango 1 : {a} "
          f"{sum(1 for x in ra.values() if x['rank_pag'] == 1)}/{len(items)}   |   "
          f"{b} {sum(1 for x in rb.values() if x['rank_pag'] == 1)}/{len(items)}")
    print(f"chunk 'quasi giusti' nei top{LIMIT} (somma): {a} "
          f"{sum(x['quasi'] for x in ra.values())}   |   "
          f"{b} {sum(x['quasi'] for x in rb.values())}")
    print(f"\nPEGGIORATI: {peggiorati or 'nessuno'}")
    print(f"MIGLIORATI: {migliorati or 'nessuno'}")
    print("\nESITO: " + ("ramo code NON peggiorato" if not peggiorati
                         else "ATTENZIONE — il ramo code peggiora, vedi sopra"))

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"a": a, "b": b, "rows_a": list(ra.values()), "rows_b": list(rb.values())},
            indent=2, ensure_ascii=False), encoding="utf-8")
    return 1 if peggiorati else 0


if __name__ == "__main__":
    raise SystemExit(main())
