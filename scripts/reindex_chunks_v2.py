"""Re-indicizzazione dei vettori su una collezione PARALLELA (A/B su cosa entra
nell'indice: identita' del documento nel denso, codice atomico nello sparse).

Non ri-estrae e non ri-chunka NULLA: il payload di `aike_chunks` contiene gia'
tutto quello che serve alla text-prep (`desc`, `code`, `pos`, `doc`, `page`).
Si rileggono i payload, si ricalcolano i vettori con la text-prep NUOVA e si
scrivono su una collezione diversa. Il confronto poi e' fra due indici che
differiscono SOLO per cosa e' stato indicizzato.

GARANZIA DI NON-SCRITTURA SULLA PRODUZIONE
    L'unica collezione letta e' quella sorgente; le uniche scritte sono le
    destinazioni, e il programma RIFIUTA di partire se una destinazione
    coincide con la sorgente (`_assert_destinazioni_sicure`). Nessun `delete`,
    nessun `drop`: solo `create` (se manca) e `upsert`.

DUE DESTINAZIONI IN UN SOLO PASSAGGIO
    Il costo dominante e' il denso (BGE-M3, ~125 chunk/s): il BM25 e'
    tokenizzazione pura, praticamente gratis. Calcolando il denso UNA volta e
    scrivendolo su due collezioni che differiscono solo per lo stemmer BM25
    (italiano = quello di produzione, inglese = il candidato, visto che il
    corpus e' 72.7% EN), la domanda sulla lingua dello stemmer si misura senza
    un secondo passaggio da mezz'ora.

RIPARTENZA
    Un checkpoint JSON tiene l'offset di scroll (che in Qdrant e' un id di
    punto) e i conteggi. Interrompendo con Ctrl-C e rilanciando, riparte dal
    punto in cui era. `--restart` riparte da zero (senza cancellare nulla:
    gli id sono deterministici, il riscritto sovrascrive identico).

Uso:
    .venv/bin/python scripts/reindex_chunks_v2.py
    .venv/bin/python scripts/reindex_chunks_v2.py --limit 2000   # prova rapida
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.common.run_config import load_runtime_env  # noqa: E402

load_runtime_env()

from qdrant_client import models  # noqa: E402

from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.ingestion.embedding import (  # noqa: E402
    COLLECTION,
    DENSE_DIM,
    DENSE_NAME,
    SPARSE_NAME,
    dense_text,
    embed_dense,
    embed_sparse_docs,
    sparse_text,
)

SORGENTE = COLLECTION
# destinazione -> lingua dello stemmer BM25
DESTINAZIONI = {"aike_chunks_v2": "italian", "aike_chunks_v2en": "english"}
CHECKPOINT = ROOT / "data" / "eval" / "reindex_v2_checkpoint.json"
CHECKPOINT_SPARSE = ROOT / "data" / "eval" / "reindex_v2_sparse_checkpoint.json"
SCROLL_BATCH = 1000
DENSE_BATCH = 32
UPSERT_BATCH = 256
_KEYWORD_INDEXES = ("code", "lang", "doc", "doc_stem", "status", "parent_id",
                    "version_status", "ruoli_consentiti")


def _assert_destinazioni_sicure(destinazioni: dict[str, str]) -> None:
    """Fail-closed: mai scrivere sulla collezione di produzione."""
    for dest in destinazioni:
        if dest == SORGENTE:
            raise SystemExit(
                f"RIFIUTO: la destinazione '{dest}' e' la collezione sorgente. "
                "Questo script non scrive mai su di essa.")


def ensure_collection(qc, nome: str) -> None:
    """Crea la collezione + gli indici payload se manca. Mai drop, mai delete.

    Gli indici keyword sono gli stessi della produzione: senza `version_status`
    e `ruoli_consentiti` indicizzati i filtri RBAC/versione del retrieval si
    comporterebbero in modo diverso e l'A/B misurerebbe anche quello.
    """
    if not qc.collection_exists(nome):
        qc.create_collection(
            collection_name=nome,
            vectors_config={DENSE_NAME: models.VectorParams(
                size=DENSE_DIM, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE_NAME: models.SparseVectorParams(
                modifier=models.Modifier.IDF)},
        )
    for field in _KEYWORD_INDEXES:
        try:
            qc.create_payload_index(nome, field_name=field,
                                    field_schema=models.PayloadSchemaType.KEYWORD)
        except Exception:
            pass
    try:
        qc.create_payload_index(nome, field_name="is_assembly",
                                field_schema=models.PayloadSchemaType.BOOL)
    except Exception:
        pass


def _carica_checkpoint(restart: bool, percorso: Path) -> dict:
    if restart or not percorso.exists():
        return {"offset": None, "fatti": 0, "con_dense": 0, "secondi": 0.0}
    return json.loads(percorso.read_text(encoding="utf-8"))


def _salva_checkpoint(stato: dict, percorso: Path) -> None:
    percorso.parent.mkdir(parents=True, exist_ok=True)
    percorso.write_text(json.dumps(stato, indent=2), encoding="utf-8")


def _sparse_vector(v) -> models.SparseVector:
    return models.SparseVector(indices=v.indices.tolist(), values=v.values.tolist())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restart", action="store_true", help="riparte da zero")
    ap.add_argument("--limit", type=int, default=0,
                    help="ferma dopo N punti (prova rapida)")
    ap.add_argument("--dest", nargs="*", default=None,
                    help="sottoinsieme delle destinazioni")
    ap.add_argument("--solo-sparse", action="store_true",
                    help="riscrive SOLO i vettori sparse, lasciando intatti i "
                         "densi e il payload gia' calcolati (update_vectors sul "
                         "solo vettore nominato): serve quando cambia la "
                         "text-prep lessicale e non quella semantica")
    args = ap.parse_args()

    destinazioni = ({d: DESTINAZIONI[d] for d in args.dest} if args.dest
                    else dict(DESTINAZIONI))
    _assert_destinazioni_sicure(destinazioni)

    qc = get_qdrant_client()
    totale = qc.count(SORGENTE, exact=True).count
    for nome in destinazioni:
        ensure_collection(qc, nome)

    percorso_ck = CHECKPOINT_SPARSE if args.solo_sparse else CHECKPOINT
    stato = _carica_checkpoint(args.restart, percorso_ck)
    offset = stato["offset"]
    fatti, con_dense = stato["fatti"], stato["con_dense"]
    trascorso_prima = stato["secondi"]

    print(f"sorgente   {SORGENTE}: {totale} punti")
    for nome, lang in destinazioni.items():
        print(f"destinazione {nome}: stemmer BM25 = {lang} "
              f"(gia' presenti {qc.count(nome, exact=True).count} punti)")
    print(f"modalita'    : {'SOLO SPARSE (densi e payload intatti)' if args.solo_sparse else 'completa (denso + sparse + payload)'}")
    print(f"ripresa da offset={offset!r}, gia' fatti {fatti}\n")

    t0 = time.monotonic()
    fatti_in_questo_run = 0
    vuoti = [0]  # punti senza alcun termine lessicale (saltati in --solo-sparse)
    try:
        while True:
            recs, prossimo = qc.scroll(SORGENTE, limit=SCROLL_BATCH, offset=offset,
                                       with_payload=True, with_vectors=False)
            if not recs:
                break

            payloads = [r.payload for r in recs]
            if args.solo_sparse:
                # i densi non dipendono dalla text-prep lessicale: ricalcolarli
                # sarebbe mezz'ora di GPU per riscrivere numeri identici
                d_texts = [None] * len(payloads)
                idx_dense = []
                dense_by_idx = {}
            else:
                # denso: SOLO dove c'e' una descrizione (contratto invariato)
                d_texts = [dense_text(p) for p in payloads]
                idx_dense = [i for i, t in enumerate(d_texts) if t]
                dense_by_idx = dict(zip(idx_dense, embed_dense(
                    [d_texts[i] for i in idx_dense], batch_size=DENSE_BATCH)))

            s_texts = [sparse_text(p) for p in payloads]
            sparse_per_lingua = {lang: embed_sparse_docs(s_texts, language=lang)
                                 for lang in set(destinazioni.values())}

            for nome, lang in destinazioni.items():
                sparse_vecs = sparse_per_lingua[lang]
                if args.solo_sparse:
                    # update_vectors tocca il SOLO vettore nominato: densi e
                    # payload restano esattamente quelli gia' calcolati.
                    # I punti il cui testo lessicale non produce ALCUN termine
                    # (niente code, niente pos, desc fatta di sola punteggiatura)
                    # vanno saltati: `update_vectors` rifiuta un vettore vuoto
                    # (422). Sono gli stessi che erano vuoti anche prima — la
                    # text-prep additiva non toglie termini a nessuno, quindi
                    # saltarli non lascia indietro niente di aggiornabile.
                    aggiornamenti = []
                    for i, r in enumerate(recs):
                        if len(sparse_vecs[i].indices) == 0:
                            vuoti[0] += 1
                            continue
                        aggiornamenti.append(models.PointVectors(
                            id=r.id,
                            vector={SPARSE_NAME: _sparse_vector(sparse_vecs[i])}))
                    for s in range(0, len(aggiornamenti), UPSERT_BATCH):
                        qc.update_vectors(nome, points=aggiornamenti[s:s + UPSERT_BATCH],
                                          wait=True)
                    continue
                punti = []
                for i, r in enumerate(recs):
                    vector: dict = {SPARSE_NAME: _sparse_vector(sparse_vecs[i])}
                    if i in dense_by_idx:
                        vector[DENSE_NAME] = dense_by_idx[i]
                    # `desc_search` E' per definizione il testo del canale denso:
                    # riallineato, altrimenti il payload direbbe una cosa e il
                    # vettore ne direbbe un'altra.
                    punti.append(models.PointStruct(
                        id=r.id, vector=vector,
                        payload=dict(payloads[i], desc_search=d_texts[i])))
                for s in range(0, len(punti), UPSERT_BATCH):
                    # wait=True: il checkpoint viene salvato subito dopo, e un
                    # checkpoint che dichiara scritto cio' che e' ancora nel WAL
                    # renderebbe la ripartenza silenziosamente lacunosa.
                    qc.upsert(nome, points=punti[s:s + UPSERT_BATCH], wait=True)

            fatti += len(recs)
            fatti_in_questo_run += len(recs)
            con_dense += len(idx_dense)
            offset = prossimo
            trascorso = time.monotonic() - t0
            rate = fatti_in_questo_run / max(trascorso, 1e-9)
            resto = max(totale - fatti, 0)
            print(f"  {fatti:>7}/{totale}  ({100 * fatti / totale:5.1f}%)  "
                  f"denso {con_dense}  {rate:6.1f} chunk/s  "
                  f"ETA {resto / max(rate, 1e-9) / 60:5.1f} min", flush=True)
            _salva_checkpoint({"offset": offset, "fatti": fatti,
                               "con_dense": con_dense,
                               "secondi": trascorso_prima + trascorso}, percorso_ck)
            if offset is None or (args.limit and fatti_in_questo_run >= args.limit):
                break
    except KeyboardInterrupt:
        print("\ninterrotto: il checkpoint e' salvato, rilancia per riprendere")
        return 130

    trascorso = time.monotonic() - t0
    if args.solo_sparse:
        print(f"\npunti senza alcun termine lessicale, saltati: {vuoti[0]}")
    print(f"\nfatto: {fatti} punti ({con_dense} con vettore denso) in "
          f"{trascorso / 60:.1f} min di questo run "
          f"({(trascorso_prima + trascorso) / 60:.1f} min totali)")
    for nome in destinazioni:
        print(f"  {nome}: {qc.count(nome, exact=True).count} punti")
    print(f"  {SORGENTE}: {qc.count(SORGENTE, exact=True).count} punti "
          "(sorgente, mai scritta)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
