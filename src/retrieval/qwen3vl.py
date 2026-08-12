"""Fase 1.1 — Backend sentence-transformers per l'embedder visivo Qwen3-VL.

Backend unificato per PixelRAG: embedding visivi (single-vector MRL) +
gestione collection Qdrant + ricerca Cosine. Sostituisce completamente il vecchio
`src/retrieval/visual.py` e il paradigma multivettore MaxSim.

Caricamento lazy (`lru_cache`): costruire l'adapter (e testarlo con fake) non
importa mai torch/sentence_transformers; il backend pesante si tocca solo alla
prima chiamata reale, dopo che il ModelMultiplexer ha garantito lo spazio RAM/VRAM
(eviction dei modelli inattivi).
"""

from __future__ import annotations

import re
from functools import lru_cache

QWEN3VL_MODEL = "Qwen/Qwen3-VL-Embedding-2B"
EMBED_DIM = 1024  # MRL: tronca a 1024
RENDER_DPI = 150
MAX_SIDE_PX = 1540
QUERY_INSTRUCTION = "Given a visual query, find the corresponding image."


@lru_cache(maxsize=1)
def _model():
    """Carica modello sentence-transformers (lazy)."""
    from sentence_transformers import SentenceTransformer

    # trust_remote_code is necessary for Qwen3-VL models
    model = SentenceTransformer(QWEN3VL_MODEL, trust_remote_code=True)
    return model


def embed_pages(images) -> list[list[float]]:
    """Vettore singolo MRL-1024 per una lista di PIL.Image (batch piccoli!).

    Lista di immagini -> lista di vettori (1024-d).
    """
    import torch
    import torch.nn.functional as F

    model = _model()
    # convert_to_tensor to use PyTorch tensor operations
    embeddings = model.encode(images, convert_to_tensor=True)
    
    # MRL: tronca a 1024 e normalizza (L2) per distance=COSINE
    embeddings_trunc = embeddings[:, :EMBED_DIM]
    embeddings_norm = F.normalize(embeddings_trunc, p=2, dim=1)
    return embeddings_norm.to(torch.float32).cpu().numpy().tolist()


def embed_query(text: str) -> list[float]:
    """Vettore singolo della query testuale (MRL-1024) con instruction."""
    import torch
    import torch.nn.functional as F

    model = _model()
    # model is instruction-aware
    embeddings = model.encode([text], prompt_name="query", convert_to_tensor=True)
    
    # MRL: tronca a 1024 e normalizza
    embeddings_trunc = embeddings[:, :EMBED_DIM]
    embeddings_norm = F.normalize(embeddings_trunc, p=2, dim=1)
    return embeddings_norm[0].to(torch.float32).cpu().numpy().tolist()


# ----------------------------------------------------------- Qdrant collection

VISUAL_COLLECTION = "aike_pages_visual"


def ensure_visual_collection(client, recreate: bool = False,
                             collection: str = VISUAL_COLLECTION) -> None:
    """Crea (o ricrea) la collection visiva single-vector su Qdrant.

    Un punto = un TILE. HNSW attivo, distance COSINE, size EMBED_DIM (1024).
    """
    from qdrant_client import models

    if recreate and client.collection_exists(collection):
        client.delete_collection(collection)
    if not client.collection_exists(collection):
        client.create_collection(
            collection_name=collection,
            vectors_config=models.VectorParams(
                size=EMBED_DIM,
                distance=models.Distance.COSINE,
            ),
        )
        for f in ("doc", "cat", "page", "tile"):
            client.create_payload_index(
                collection, field_name=f,
                field_schema=models.PayloadSchemaType.INTEGER if f in ("page", "tile")
                else models.PayloadSchemaType.KEYWORD)


@lru_cache(maxsize=1)
def _indexed_docs_cache() -> tuple[str, ...]:
    """Cache degli stem documenti indicizzati nella collection visiva."""
    from src.backend.clients.qdrant_client import get_qdrant_client

    client = get_qdrant_client()
    docs, offset = set(), None
    while True:
        pts, offset = client.scroll(VISUAL_COLLECTION, limit=512, offset=offset,
                                    with_payload=["doc"])
        docs.update(p.payload["doc"] for p in pts)
        if offset is None:
            return tuple(sorted(docs))


HINT_MIN_TOKEN_LEN = 3


def _word_parts(text: str) -> list[str]:
    """Spezza su tutto cio' che non e' alfanumerico -> parole minuscole.

    Stessa funzione per la query e per lo stem del documento, cosi' i due lati si
    confrontano nello stesso alfabeto: `kbs_gastrotechnik_4055041` ->
    {kbs, gastrotechnik, 4055041}, `(KBS Gastrotechnik)` -> {kbs, gastrotechnik}.
    """
    return [p for p in re.split(r"[^0-9a-zA-Z]+", text.lower()) if p]


def doc_hint_filter(query: str):
    """Filtro deterministico: token della query che nominano un DOCUMENTO indicizzato.

    Un token e' un hint se coincide con una PAROLA INTERA del nome-documento.
    Due difetti strutturali corretti il 12/08, entrambi misurati sui set di eval:

    1. Il match era per SOTTOSTRINGA sullo stem intero, quindi 'der' (articolo
       tedesco, 3 lettere) pescava `as_schneider`, `centro_siderurgico` e
       `crusader_marine`. Confrontare PAROLE INTERE elimina la classe di rumore
       senza bisogno di una stoplist: e' il corpus a dire cosa e' una parola, non
       un elenco da manutenere.
    2. Il pre-filtro `t.isalpha()` non scartava la punteggiatura: scartava i TOKEN
       che la contengono. Su «... (KBS Gastrotechnik)» buttava via proprio i due
       token buoni e teneva 'der', producendo un filtro che ESCLUDEVA il documento
       atteso — un fail-closed che nasconde la risposta giusta. Ora la
       punteggiatura si normalizza (`_word_parts`), e i token numerici o misti
       ('BL200', '4520R') diventano hint legittimi invece di essere scartati.

    Nota sulla document-frequency: si e' valutato di scartare i token che matchano
    "troppi" documenti (rumore di genere: 'catalogo' df 16, 'listino' df 15). NON
    adottato: qualunque soglia stretta abbastanza da tagliare quel rumore (df < 5)
    ributta fuori i marchi veri, perche' 'kbs', 'gastrotechnik' e 'iwaki' hanno df
    esattamente 5 — e reintroduce il difetto 2 su V009 e CV16. La df confonde
    "marchio con molti cataloghi" con "parola di genere".

    Fail-OPEN: se nessun documento matcha si torna None (ricerca globale), mai un
    filtro vuoto. Il controllo d'accesso e' l'altro filtro (RBAC), quello fail-closed.
    """
    from qdrant_client import models

    tokens = {t for t in _word_parts(query) if len(t) >= HINT_MIN_TOKEN_LEN}
    matched = [d for d in _indexed_docs_cache()
               if tokens & set(_word_parts(d))]
    if not matched:
        return None
    return models.Filter(must=[models.FieldCondition(
        key="doc", match=models.MatchAny(any=matched))])



