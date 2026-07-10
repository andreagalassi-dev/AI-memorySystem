"""Fase 1.1 — Port del motore vettoriale visivo + Fake DB-free + ricomposizione pagina.

Il vector store reale (Qdrant, collection `aike_pages_visual`, comparator MaxSim) sta
DIETRO il Port `VisualIndex`, come GLM-OCR dietro `TableExtractor` in 1.2. Cosi'
tiling, ricomposizione e (in seguito) fusione ibrida si sviluppano e testano contro
un `FakeVisualIndex` in memoria, senza Qdrant ne' GPU.

Granularita': un punto = un TILE (o un thumbnail) di pagina. La ricerca restituisce
`VisualHit` a livello tile; `pages_from_tiles` li **ricompone sulla tupla (doc, page)**
prendendo il **MaxSim** (score pagina = massimo tra i suoi tile). E' il secondo
livello di MaxSim: dentro il tile lo calcola l'indice (patch-level, se multivettore);
tra i tile della pagina lo calcoliamo qui.

Design: docs/superpowers/specs/2026-07-09-fase1.1-pixelrag-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence, runtime_checkable

KIND_TILE = "tile"
KIND_THUMB = "thumb"


@dataclass(frozen=True)
class VisualPoint:
    """Punto visivo indicizzabile: un tile o un thumbnail di pagina.

    `vector` e' opaco all'indice (single-vector o multivettore, a seconda
    dell'embedder). `payload` porta le chiavi RBAC/versioning del progetto
    (`ruoli_consentiti`, `version_status`) + provenienza (bbox, ...).
    """

    point_id: str
    doc: str
    page: int
    kind: str                       # KIND_TILE | KIND_THUMB
    row: int
    col: int
    vector: Sequence[float]
    payload: dict = field(default_factory=dict)


@dataclass(frozen=True)
class VisualHit:
    """Risultato di ricerca a livello TILE."""

    point_id: str
    doc: str
    page: int
    kind: str
    row: int
    col: int
    score: float


@dataclass(frozen=True)
class PageHit:
    """Risultato ricomposto a livello PAGINA (score = MaxSim dei tile della pagina)."""

    doc: str
    page: int
    score: float
    n_tiles: int                    # quanti tile recuperati hanno contribuito (audit)


@runtime_checkable
class VisualIndex(Protocol):
    """Contratto del motore vettoriale visivo (implementato da Qdrant o dal Fake)."""

    def upsert(self, points: Sequence[VisualPoint]) -> int:
        ...

    def search(self, query_vector: Sequence[float], k: int,
               *, flt: dict | None = None) -> list[VisualHit]:
        ...


def pages_from_tiles(hits: Sequence[VisualHit]) -> list[PageHit]:
    """Ricompone gli hit-tile in hit-pagina: score pagina = MaxSim dei suoi tile.

    Raggruppa per `(doc, page)`, prende il massimo score tra i tile recuperati di
    quella pagina, ordina per score decrescente (tie-break stabile su doc, page).
    """
    best: dict[tuple[str, int], list[float]] = {}
    for h in hits:
        best.setdefault((h.doc, h.page), []).append(h.score)
    pages = [PageHit(doc=doc, page=page, score=max(scores), n_tiles=len(scores))
             for (doc, page), scores in best.items()]
    pages.sort(key=lambda p: (-p.score, p.doc, p.page))
    return pages


def _matches(payload: dict, flt: dict | None) -> bool:
    """Filtro payload generico: uguaglianza scalare, o appartenenza per i campi lista.

    Serve a riprodurre in memoria il filtro RBAC/versioning (`ruoli_consentiti`
    lista -> 'contiene il ruolo'; `version_status` scalare -> uguaglianza).
    """
    if not flt:
        return True
    for key, want in flt.items():
        got = payload.get(key)
        if isinstance(got, (list, tuple, set)):
            if want not in got:
                return False
        elif got != want:
            return False
    return True


class FakeVisualIndex:
    """Indice visivo in memoria per i test: upsert + ricerca per prodotto scalare.

    Stand-in DETERMINISTICO e DB-free. Lo scoring e' un semplice dot-product su
    vettori densi (il MaxSim patch-level del modello reale vive in Qdrant, opaco al
    Port): sufficiente per esercitare upsert -> top-k -> filtro -> ricomposizione.
    """

    def __init__(self) -> None:
        self._points: dict[str, VisualPoint] = {}

    def upsert(self, points: Sequence[VisualPoint]) -> int:
        for p in points:
            self._points[p.point_id] = p   # id deterministico -> re-upsert sovrascrive
        return len(points)

    def search(self, query_vector: Sequence[float], k: int,
               *, flt: dict | None = None) -> list[VisualHit]:
        scored: list[VisualHit] = []
        for p in self._points.values():
            if not _matches(p.payload, flt):
                continue
            score = sum(a * b for a, b in zip(query_vector, p.vector))
            scored.append(VisualHit(point_id=p.point_id, doc=p.doc, page=p.page,
                                    kind=p.kind, row=p.row, col=p.col, score=score))
        scored.sort(key=lambda h: (-h.score, h.point_id))
        return scored[:k]


DEFAULT_VISUAL_COLLECTION = "aike_pages_visual"


class QdrantVisualIndex:
    """Adapter reale `VisualIndex` su Qdrant (Fase 1.3, ramo VISUAL).

    Punta di default sulla collection `aike_pages_visual` (P2.7/P4): un punto = una
    PAGINA, multivettore Qwen3-VL, comparator MaxSim. Client e collection INIETTATI
    (come `PgSqlExecutor`): l'adapter e' testabile su una collection usa-e-getta.

    Il filtro RBAC/versioning generico (`{ruoli_consentiti, version_status}`, prodotto
    da `search.access_filter`) viene tradotto in `models.Filter` con `MatchValue`: su
    un campo-lista Qdrant fa il "contiene", quindi `ruoli_consentiti=<ruolo>` matcha le
    pagine che ammettono quel ruolo. Fail-closed: nessun default che apra l'accesso —
    se il ruolo non compare, la pagina non torna. La ricomposizione pagina (MaxSim tra
    tile) la fa Qdrant; `pages_from_tiles` a valle e' un pass-through (1 hit = 1 pagina).
    """

    def __init__(self, client, collection: str = DEFAULT_VISUAL_COLLECTION):
        self._client = client
        self._collection = collection

    @staticmethod
    def _to_filter(flt: dict | None):
        from qdrant_client import models
        if not flt:
            return None
        must = [models.FieldCondition(key=key, match=models.MatchValue(value=str(val)))
                for key, val in flt.items() if val is not None]
        return models.Filter(must=must) if must else None

    def upsert(self, points: Sequence[VisualPoint]) -> int:
        from qdrant_client import models
        structs = [models.PointStruct(id=p.point_id, vector=p.vector, payload=p.payload)
                   for p in points]
        if structs:
            self._client.upsert(self._collection, points=structs)
        return len(structs)

    def search(self, query_vector: Sequence[float], k: int,
               *, flt: dict | None = None) -> list[VisualHit]:
        res = self._client.query_points(
            self._collection, query=query_vector, query_filter=self._to_filter(flt),
            limit=k, with_payload=True)
        hits = []
        for sp in res.points:
            pl = sp.payload or {}
            hits.append(VisualHit(
                point_id=str(sp.id), doc=pl.get("doc"), page=pl.get("page"),
                kind=pl.get("kind", KIND_THUMB), row=pl.get("row", -1),
                col=pl.get("col", -1), score=sp.score))
        return hits
