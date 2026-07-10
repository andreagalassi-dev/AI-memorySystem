"""P4.2 — Backfill ADDITIVO dei payload Qdrant dalla SSOT Postgres.

Proietta su `aike_chunks` e `aike_pages_visual` le 3 chiavi nuove
(`version_status`, `doc_version`, `ruoli_consentiti`) lette dal registro
(P4.1). set_payload con selettore-filtro su `doc` = MERGE delle chiavi:
vettori, chiavi esistenti e indici esistenti restano intatti. IDEMPOTENTE.

Prerequisiti: docker compose up -d postgres qdrant; registro popolato
(uv run scripts/registry_populate.py).

Sequenza fail-fast (exit 1 al primo check rosso):
  [1/6] export SSOT: 57 record, tutti con ruoli e stato valido
  [2/6] copertura: ogni doc stem di Qdrant esiste nel registro
  [3/6] snapshot pre-patch (payload campione + top-1 retrieval di riferimento)
  [4/6] patch per stem su entrambe le collection + nuovi indici keyword
  [5/6] verifiche dati: count per version_status/ruolo == attesi dal registro
  [6/6] contratti P3 intatti: filtro `code` del guardrail + top-1 invariato

Uso: uv run scripts/backfill_qdrant.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdrant_client import models  # noqa: E402

from src.backend.clients.postgres_client import get_pg_conn  # noqa: E402
from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.common.roles import Role  # noqa: E402
from src.ingestion.embedding import COLLECTION, DENSE_NAME, embed_dense  # noqa: E402
from src.registry.registry import backfill_payload, export_backfill  # noqa: E402
from src.registry.sync import NEW_KEYS, facet_docs, sync_collection  # noqa: E402
from src.retrieval.qwen3vl import VISUAL_COLLECTION  # noqa: E402

COLLECTIONS = (COLLECTION, VISUAL_COLLECTION)
SMOKE_QUERY = "codice ricambio cilindro"  # riferimento arbitrario ma FISSO


def die(msg: str) -> None:
    print(f"  ERRORE: {msg}")
    sys.exit(1)


def count(qc, coll: str, key: str, value) -> int:
    return qc.count(coll, exact=True, count_filter=models.Filter(
        must=[models.FieldCondition(key=key, match=models.MatchValue(value=value))])).count


def dense_exact_top(qc, query: str, limit: int = 3) -> list[tuple]:
    """Smoke deterministico: canale denso con ricerca ESATTA (niente HNSW).

    La ricerca approssimata ha wobble di recall al confine del top-k anche a
    dati invariati (osservato: 3° hit diverso tra chiamate consecutive);
    l'esatta e' riproducibile. Triple ordinate canonicamente: chunk
    quasi-duplicati (opacmare p214/p215) hanno score identici e ordine libero.
    """
    hits = qc.query_points(
        COLLECTION, query=embed_dense([query])[0], using=DENSE_NAME,
        with_payload=["doc", "page"], limit=limit,
        search_params=models.SearchParams(exact=True)).points
    return sorted((h.payload["doc"], h.payload["page"], round(h.score, 6))
                  for h in hits)


def sample_payload(qc, coll: str) -> tuple:
    pts, _ = qc.scroll(coll, limit=1, with_payload=True, with_vectors=False)
    return pts[0].id, pts[0].payload


def main() -> None:
    print("[1/6] export SSOT da Postgres")
    try:
        conn = get_pg_conn()
        export = export_backfill(conn)
        conn.close()
    except Exception as exc:
        die(f"Postgres non disponibile: {exc}")
    if not export:
        die("registro vuoto: eseguire prima registry_populate.py")
    payloads = {}
    for stem, rec in sorted(export.items()):
        payloads[stem] = backfill_payload(rec)  # fail-closed su record incoerenti
        if Role.TECNICO.value not in payloads[stem]["ruoli_consentiti"]:
            die(f"{stem}: manca 'tecnico' nei ruoli — seed incoerente")
    print(f"  {len(export)} record, tutti con ruoli e stato validi")

    qc = get_qdrant_client()
    print("[2/6] copertura registro <-> Qdrant")
    for coll in COLLECTIONS:
        orphans = facet_docs(qc, coll) - set(export)
        if orphans:
            die(f"{coll}: doc stems fuori registro (la SSOT ha buchi): "
                f"{sorted(orphans)[:5]}")
        print(f"  {coll}: nessun orfano")

    print("[3/6] snapshot pre-patch")
    before = {coll: sample_payload(qc, coll) for coll in COLLECTIONS}
    top_before = dense_exact_top(qc, SMOKE_QUERY)
    n_points = {coll: qc.count(coll, exact=True).count for coll in COLLECTIONS}
    print(f"  punti: {n_points} | smoke dense: {top_before}")

    print("[4/6] riconciliazione selettiva + indici keyword")
    for coll in COLLECTIONS:
        rep = sync_collection(qc, coll, export)  # patcha SOLO gli stem divergenti
        for key in ("version_status", "ruoli_consentiti"):
            qc.create_payload_index(coll, field_name=key,
                                    field_schema=models.PayloadSchemaType.KEYWORD,
                                    wait=True)
        print(f"  {coll}: {len(rep.patched)} patchati, {rep.skipped} gia' "
              f"allineati, indici ok")

    print("[5/6] verifiche dati (attesi calcolati dal registro)")
    for coll in COLLECTIONS:
        stems = facet_docs(qc, coll)
        n_cur = count(qc, coll, "version_status", "current")
        if n_cur != n_points[coll]:
            die(f"{coll}: current={n_cur}, attesi {n_points[coll]}")
        for role in Role:
            expected = sum(count(qc, coll, "doc", s) for s in stems
                           if role.value in payloads[s]["ruoli_consentiti"])
            got = count(qc, coll, "ruoli_consentiti", role.value)
            if got != expected:
                die(f"{coll}: ruolo {role.value} -> {got}, attesi {expected}")
        pid, old = before[coll]
        now = qc.retrieve(coll, ids=[pid], with_payload=True)[0].payload
        # si confrontano i payload SENZA le 3 chiavi additive da entrambi i
        # lati: alla seconda esecuzione (idempotente) lo snapshot le contiene gia'
        strip = lambda p: {k: v for k, v in p.items() if k not in NEW_KEYS}  # noqa: E731
        if strip(now) != strip(old) or any(k not in now for k in NEW_KEYS):
            die(f"{coll}: payload campione alterato oltre le 3 chiavi additive")
        print(f"  {coll}: current 100%, ruoli coerenti, campione intatto (+3 chiavi)")

    print("[6/6] contratti P3 intatti")
    schema = qc.get_collection(COLLECTION).payload_schema
    for k in ("code", "doc", "lang", "status"):
        if k not in schema:
            die(f"indice preesistente {k} sparito da {COLLECTION}")
    code_hits = qc.facet(COLLECTION, key="code", limit=1, exact=True).hits
    if not code_hits:
        die("nessun codice nell'indice `code`: gancio guardrail vuoto")
    sample_code = code_hits[0].value
    if count(qc, COLLECTION, "code", sample_code) < 1:
        die(f"filtro esatto su code rotto per {sample_code!r}")
    top_after = dense_exact_top(qc, SMOKE_QUERY)
    if top_after != top_before:
        die(f"retrieval smoke cambiato: {top_before} -> {top_after}")
    print(f"  filtro code ok ({sample_code!r}) | top-3 smoke invariato")
    print("BACKFILL COMPLETATO: Qdrant e' proiezione della SSOT")


if __name__ == "__main__":
    main()
