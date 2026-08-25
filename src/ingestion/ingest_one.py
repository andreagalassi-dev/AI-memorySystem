"""Fase B (B2/B3) — Ingestion RIUSABILE di UN SOLO documento.

Il percorso manuale attuale (`scripts/ingest_chunks.py`) fa `drop+create` GLOBALE
della collezione a ogni run: inadatto ad aggiungere un solo file (vedi
`docs/ingestion-pipeline-attuale.md`). Qui costruiamo la funzione per-documento che
il Watchdog (B4) chiamera' su ogni file rilevato.

Orchestrazione (riusa gli entry-point esistenti, non copia logica):
  estrazione (Docling, ponte env isolato) -> chunking difensivo per pagina
  (`corpus_chunks.chunk_page`, gia' con `parent_id`/`is_assembly` e profilo brand)
  -> embedding dense BGE-M3 + sparse BM25 (`ingestion.embedding`) -> **upsert**
  (mai drop) in Qdrant con payload RBAC/versioning (`doc_stem`, `ruoli_consentiti`,
  `version_status='current'`, `page`, `code`, `parent_id`, `is_assembly`) ->
  registrazione versione+ruoli in Postgres (`registry.register_document`).

Idempotenza:
  - id punto deterministico (`embedding.point_id`, uuid5 del chunk_id): re-ingest
    dello STESSO contenuto sovrascrive gli stessi punti, niente duplicati;
  - B3: `delete_document_points` cancella i punti del `doc_stem` PRIMA dell'upsert,
    cosi' un contenuto MODIFICATO non lascia orfani della versione precedente.

Regola d'oro del progetto rispettata: nessun codice fabbricato. Il chunker emette
solo codici presenti nel sorgente (guardia `code_not_in_source`); i codici del pass
di riparazione restano `status='repaired'`/`fuzzy_code` (cappati a MEDIA a valle).
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import asdict
from pathlib import Path

from qdrant_client import models

from src.backend.clients.qdrant_client import get_qdrant_client
from src.ingestion.brands import resolve
from src.ingestion.corpus_chunks import chunk_page
from src.ingestion.embedding import (
    COLLECTION,
    DENSE_DIM,
    DENSE_NAME,
    SPARSE_NAME,
    dense_text,
    embed_dense,
    embed_sparse_docs,
    point_id,
    sparse_text,
)
from src.ingestion.lang_detect import detect_lang
from src.registry.registry import register_document

ROOT = Path(__file__).resolve().parent.parent.parent
UPSERT_BATCH = 64
# Indici payload della collezione (parita' con scripts/ingest_chunks.py + le
# chiavi RBAC/versioning che qui scriviamo direttamente all'upsert).
_KEYWORD_INDEXES = ("code", "lang", "doc", "doc_stem", "status", "parent_id",
                    "version_status", "ruoli_consentiti")


# --------------------------------------------------------------------- estrazione


def _default_extract(filepath: str, *, limit: int = 0) -> dict[int, str]:
    """Estrattore di default: PONTE all'env Docling isolato (scripts/extract_one.py).

    Docling NON e' nel venv di progetto (dip. pesante, env PEP723 separato): si
    invoca via `uv run`. E' un ponte dichiarato, da migliorare quando Docling
    diventasse una dip. diretta. Ritorna {numero_pagina(1-based) -> markdown}.
    """
    with tempfile.TemporaryDirectory(prefix="aike_extract_") as td:
        cmd = ["uv", "run", str(ROOT / "scripts" / "extract_one.py"),
               "--pdf", str(Path(filepath).resolve()), "--out", td]
        if limit:
            cmd += ["--limit", str(limit)]
        proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"estrazione Docling fallita ({filepath}): {proc.stderr or proc.stdout}")
        return {int(p.stem[1:]): p.read_text(encoding="utf-8")
                for p in sorted(Path(td).glob("p*.md"))}


# ---------------------------------------------------------------- collezione Qdrant


def ensure_collection(qc, collection: str = COLLECTION) -> None:
    """Crea la collezione + indici se assente. Mai drop: additiva per contratto."""
    if not qc.collection_exists(collection):
        qc.create_collection(
            collection_name=collection,
            vectors_config={DENSE_NAME: models.VectorParams(
                size=DENSE_DIM, distance=models.Distance.COSINE)},
            sparse_vectors_config={SPARSE_NAME: models.SparseVectorParams(
                modifier=models.Modifier.IDF)},
        )
    for field in _KEYWORD_INDEXES:
        try:
            qc.create_payload_index(collection, field_name=field,
                                    field_schema=models.PayloadSchemaType.KEYWORD)
        except Exception:
            pass  # indice gia' presente: idempotente
    try:
        qc.create_payload_index(collection, field_name="is_assembly",
                                field_schema=models.PayloadSchemaType.BOOL)
    except Exception:
        pass


def delete_document_points(qc, collection: str, doc_stem: str) -> int:
    """Cancella da Qdrant TUTTI i punti di un documento (B3, anti-orfani).

    Filtra su `doc` OPPURE `doc_stem`, e non e' pignoleria: misurato
    sull'indice vivo il 24/08, la chiave `doc_stem` NON copre il corpus.

        aike_pages_visual   con doc_stem:      0/10385   <- no-op TOTALE
        aike_pages_visual   con doc:       10385/10385
        aike_chunks_v2      con doc_stem: 166474/199447  <- ne mancano 32973
        aike_chunks_v2      con doc:      199447/199447

    Cioe': sul ramo VISIVO questa funzione non cancellava mai nulla, in nessun
    caso, senza dire niente; e sul narrativo lasciava indietro il 16.5% dei
    punti (quelli di generazione precedente). `doc` copre il 100% di entrambe le
    collezioni.

    Perche' era latente e non ha ancora fatto danno: oggi la funzione e' chiamata
    solo sul ramo narrativo come idempotenza del re-ingest, e gli id sono `uuid5`
    deterministici, quindi un re-ingest che produce le stesse chiavi SOVRASCRIVE.
    Il danno si manifesta quando l'insieme delle chiavi si RESTRINGE (meno
    pagine, ri-chunking diverso): i punti vecchi sopravvivono con
    `version_status='current'` e restano cercabili — "sbagliato in silenzio".
    E scatta subito, al 100%, appena qualcuno la usa per un rollback visivo,
    che e' la cosa naturale da fare.

    L'OR sulle due chiavi copre entrambe le generazioni senza dipendere da un
    backfill del payload. Gli altri documenti non vengono comunque mai toccati:
    entrambe le condizioni sono sullo stesso valore.
    """
    flt = models.Filter(should=[
        models.FieldCondition(key="doc", match=models.MatchValue(value=doc_stem)),
        models.FieldCondition(key="doc_stem", match=models.MatchValue(value=doc_stem)),
    ])
    before = qc.count(collection, exact=True, count_filter=flt).count
    qc.delete(collection, points_selector=models.FilterSelector(filter=flt), wait=True)
    return before


# ------------------------------------------------------------------- ingestion


def ingest_document(filepath: str, *, doc_stem: str, role: str, conn,
                    is_update: bool = False, cat: str = "unknown",
                    collection: str | None = None, extract_fn=None,
                    extract_limit: int = 0) -> dict:
    """Ingerisce UN documento: estrazione -> chunk -> embedding -> upsert -> registro.

    Args:
      filepath: percorso del file sorgente (PDF).
      doc_stem: identita' logica del documento (stem del filename).
      role: ruolo RBAC proprietario (finisce in `ruoli_consentiti`).
      conn: connessione psycopg3 al registro (SSOT Postgres).
      is_update: ACCETTATO E IGNORATO — nessuna riga del corpo lo legge. Non e'
        una svista da ripulire, ed e' bene saperlo prima di fidarsene:
          * il Watchdog lo calcola davvero (`worker.py`: `action == "modified"`)
            e lo passa (`watchdog/pipeline.py`), quindi qualcuno CREDE che
            distingua l'aggiornamento dal primo caricamento. Qui non distingue
            nulla: il documento viene reingerito allo stesso modo in entrambi i
            casi;
          * l'unico comportamento che potrebbe governare — la pulizia dei punti
            della generazione precedente — e' gia' INCONDIZIONATO
            (`delete_document_points` piu' sotto, sempre chiamato per
            idempotenza): gatarlo su questo flag renderebbe il re-ingest
            *meno* sicuro, non piu';
          * l'altro significato possibile — "questa e' una versione NUOVA che
            sostituisce la precedente" — non e' onorabile qui: un file
            modificato conserva lo STESSO `doc_stem` (deriva dal nome del file),
            quindi non e' una versione nuova per il registro, e
            `supersede_document` rifiuterebbe lo switch per stato incoerente.
            E' il buco descritto al punto 6 piu' sotto.
        Si tiene invece di rimuoverlo perche' e' l'UNICO punto in cui il sistema
        sa che un file e' stato MODIFICATO e non caricato per la prima volta:
        cancellarlo cancellerebbe l'unico segnale che serve a chi un giorno
        produrra' davvero lo stato `superseded`.
      cat: categoria (indizio cartella; default 'unknown', affinata in Fase C/B4).
      collection: collezione Qdrant (override per i test, mai droppa la reale).
      extract_fn: iniettabile per i test; default = ponte Docling isolato.
      extract_limit: se >0, estrai solo le prime N pagine (pilota/test).

    Ritorna: {doc_stem, n_chunks, n_points, profile_used, profile_warning}.
    """
    # default collezione risolto a runtime: sovrascrivibile (test/e2e isolato)
    # senza toccare la produzione, senza legare il default all'import.
    collection = collection or COLLECTION
    extract_fn = extract_fn or (lambda fp: _default_extract(fp, limit=extract_limit))
    profile = resolve(doc_stem)
    profile_warning = profile.get("_name") == "default"

    # 1. estrazione per-pagina
    pages = extract_fn(filepath)
    if not pages:
        raise RuntimeError(f"nessuna pagina estratta da {filepath}")
    lang = detect_lang("\n".join(pages[p] for p in sorted(pages)))

    # 2. chunking difensivo per pagina (parent_id/is_assembly dal profilo)
    chunks: list[dict] = []
    for page in sorted(pages):
        res = chunk_page(doc_stem, cat, page, pages[page], profile, lang)
        chunks.extend(asdict(c) for c in res["chunks"])
    if not chunks:
        raise RuntimeError(f"{doc_stem}: 0 chunk prodotti (documento senza dati usabili)")

    # 3. embedding: denso solo dove c'e' descrizione; sparse per tutti
    d_texts = [dense_text(c) for c in chunks]
    with_dense = [i for i, t in enumerate(d_texts) if t]
    dense_by_idx = dict(zip(with_dense, embed_dense([d_texts[i] for i in with_dense])))
    sparse_vecs = embed_sparse_docs([sparse_text(c) for c in chunks])

    # 4. costruzione punti con payload RBAC/versioning (scritto QUI, all'upsert)
    points = []
    for i, c in enumerate(chunks):
        vector: dict = {SPARSE_NAME: models.SparseVector(
            indices=sparse_vecs[i].indices.tolist(),
            values=sparse_vecs[i].values.tolist())}
        if i in dense_by_idx:
            vector[DENSE_NAME] = dense_by_idx[i]
        payload = dict(c, desc_search=d_texts[i], doc_stem=doc_stem,
                       ruoli_consentiti=[role], version_status="current")
        points.append(models.PointStruct(id=point_id(c["chunk_id"]),
                                         vector=vector, payload=payload))

    # 5. Qdrant: collezione garantita, pulizia anti-orfani, upsert (mai drop)
    qc = get_qdrant_client()
    ensure_collection(qc, collection)
    delete_document_points(qc, collection, doc_stem)  # idempotenza + re-ingest pulito
    for start in range(0, len(points), UPSERT_BATCH):
        qc.upsert(collection, points=points[start:start + UPSERT_BATCH], wait=True)

    # 6. registro Postgres (SSOT): versione + ruoli. register_document e'
    # idempotente (no-op se lo stem esiste gia'), e scrive SEMPRE version_status
    # 'current' (come il payload Qdrant al punto 4).
    #
    # BUCO NOTO, fra due componenti — nessuno produce lo stato 'superseded'.
    # Fino al 25/08 questa riga diceva che la sostituzione di versione
    # (`mark_superseded`) e' "responsabilita' del Watchdog/versioning": non e'
    # vero, e rimandava a un componente che non esiste. Verificato:
    # `grep -rn "mark_superseded\|supersede" src/watchdog/` -> ZERO occorrenze.
    # Il Watchdog produce solo l'altro stato, 'deleted' (`watchdog/soft_delete.py`,
    # sulla sparizione del file). L'unico scrittore di 'superseded' e'
    # `registry.supersede_document`, chiamato solo a mano da
    # `scripts/supersede_document.py` (e dal DoD `scripts/verify_p4_dod.py`).
    # Misurato il 25/08: 147 documenti su 147 sono 'current'.
    #
    # Non e' risolvibile QUI, e non e' una dimenticanza di due righe: `doc_stem`
    # e' lo stem del nome del file, quindi un listino aggiornato che conserva il
    # nome e' indistinguibile dal suo predecessore, e uno che cambia nome diventa
    # un documento SCOLLEGATO invece che la v2 del primo. Produrre 'superseded'
    # richiede prima separare l'identita' LOGICA del documento dal nome del file
    # — decisione di prodotto, non di questa funzione.
    #
    # Conseguenza operativa da tenere presente: tutta la catena a valle
    # dell'avviso "documentazione SOSTITUITA" (fallback cascade P4.4, flag di
    # `graph.pipeline`, cap di livello in `reconcile`) e' cablata e funziona, ma
    # oggi si accende SOLO se qualcuno lancia lo script a mano.
    reg = register_document(conn, slug=doc_stem, category=cat, roles=[role],
                            doc_stem=doc_stem, langs=[lang])

    return {"doc_stem": doc_stem, "n_chunks": len(chunks), "n_points": len(points),
            "profile_used": profile.get("_name"), "profile_warning": profile_warning,
            "document_id": reg.document_id}
