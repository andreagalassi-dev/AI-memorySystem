"""P2.3 — Text-prep e embedder per il dual-indexing (dense BGE-M3 + sparse BM25).

Separazione netta, per contratto:
  - le funzioni di TEXT-PREP sono pure (niente modelli, unit-testabili):
    l'embedder non vede mai chunk.text (filename/pagina = metadati di citazione)
    ne' JSON. Il denso vede SOLO la descrizione riparata; lo sparse vede
    pos + codice atomico + descrizione riparata. Qty/prezzo/doc/pagina restano
    payload, non termini di ricerca.
  - i LOADER dei modelli sono lazy (import di torch/fastembed dentro le funzioni):
    P2.4 riusera' gli stessi embedder e la stessa text-prep per le query.

Scelta sparse = BM25 (fastembed Qdrant/bm25), NON lo sparse learned di BGE-M3:
BM25 tiene il codice ricambio come token ATOMICO (match tutto-o-niente), mentre
i subword XLM-R farebbero scorare alto i codici "quasi giusti" — la classe di
errore vietata dal progetto. Il modifier IDF della collezione (validato in P2.1)
e' progettato per term-frequency BM25.
"""

from __future__ import annotations

import re
import uuid
from functools import lru_cache

# --- costanti condivise indice/query (P2.3 ingestion, P2.4 retrieval) -----------
COLLECTION = "aike_chunks"
DENSE_NAME = "dense"
DENSE_MODEL = "BAAI/bge-m3"
DENSE_DIM = 1024
SPARSE_NAME = "sparse"
SPARSE_MODEL = "Qdrant/bm25"
# Stemmer BM25: unico e condiviso tra indicizzazione e query (i codici non
# passano dallo stemmer). Gestione multi-lingua fine: decisione a P2.4 sui numeri.
BM25_LANGUAGE = "italian"

# ------------------------------------------------------------------- text-prep

# Riparazione deterministica dello small-caps mangled di Docling su P231
# ('B oCCa /S PoUt' -> 'bocca /spout'). Due sole regole sicure:
#   R1: lettera singola MAIUSCOLA + token che inizia con lettera -> join
#       ('B oCCa', 'C HroMe', 'F ixinG').
#   R2: CONSONANTE singola minuscola + token che inizia con lettera -> join
#       ('d eviatore', 'l eva', 't erMoStatiCo'). Le vocali singole e la 'x'
#       sono parole legittime IT ('cromo e oro', 'Raccordo x flessibile') e
#       non si toccano qui.
#   R3: VOCALE singola minuscola + token con una MAIUSCOLA in posizione >=1
#       ('a CCeSSori', 'e PoCa'): il misto-maiuscole interno e' la firma del
#       mangling, una parola vera dopo 'e'/'a' non ce l'ha ('e oro', 'a Testa'
#       restano intatte). Niente dizionari, niente indovinare: il mangling che
#       queste regole non coprono resta e lo misura P2.4.
_ACC = "àèéìíòóùú"
_R1 = re.compile(rf"(?<![0-9A-Za-z{_ACC}])([A-Z]) (?=[A-Za-z{_ACC}])")
_R2 = re.compile(rf"(?<![0-9A-Za-z{_ACC}])([b-df-hj-np-tv-wyz]) (?=[A-Za-z{_ACC}])")
_R3 = re.compile(rf"(?<![0-9A-Za-z{_ACC}])([aeiou]) (?=[A-Za-z][a-z{_ACC}]*[A-Z])")


def repair_desc(desc: str | None) -> str | None:
    """Ripara il mangling e normalizza a minuscolo. La desc originale resta
    intatta nel payload: questa forma serve solo come input di ricerca."""
    if not desc:
        return None
    s = desc
    for _ in range(4):  # fixpoint: sequenze tipo 'S tyloS -C roMo' in un colpo
        new = _R3.sub(r"\1", _R2.sub(r"\1", _R1.sub(r"\1", s)))
        if new == s:
            break
        s = new
    return re.sub(r"\s+", " ", s).lower().strip() or None


def dense_text(chunk: dict) -> str | None:
    """Input del canale SEMANTICO: solo la descrizione riparata.

    Niente codice (subword-rumore: dei codici si occupa BM25), niente
    pos/qty/prezzo/filename. Senza descrizione -> None: il punto non avra' il
    vettore denso e restera' recuperabile dal canale lessicale.
    """
    return repair_desc(chunk.get("desc"))


def sparse_text(chunk: dict) -> str:
    """Input del canale LESSICALE: pos + codice atomico + descrizione riparata."""
    parts = [chunk.get("pos"), chunk["code"], repair_desc(chunk.get("desc"))]
    return " ".join(p for p in parts if p)


def point_id(chunk_id: str) -> str:
    """ID punto deterministico: stesso chunk = stesso punto, sempre."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


# ------------------------------------------------------------- modelli (lazy)


@lru_cache(maxsize=1)
def get_dense_model():
    from FlagEmbedding import BGEM3FlagModel

    return BGEM3FlagModel(DENSE_MODEL, use_fp16=False)


@lru_cache(maxsize=1)
def get_sparse_model():
    from fastembed import SparseTextEmbedding

    return SparseTextEmbedding(SPARSE_MODEL, language=BM25_LANGUAGE)


def embed_dense(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    out = get_dense_model().encode(texts, batch_size=batch_size, max_length=512)
    return [v.tolist() for v in out["dense_vecs"]]


def embed_sparse_docs(texts: list[str]):
    """Embedding sparse lato documento (term frequencies; l'IDF lo applica Qdrant)."""
    return list(get_sparse_model().embed(texts))


def embed_sparse_query(text: str):
    """Embedding sparse lato query (stessa tokenizzazione/stemmer dell'indice)."""
    return list(get_sparse_model().query_embed(text))[0]
