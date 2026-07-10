"""Fase 2.1 — Merge relazionale Catalogo x Listino (cucitura deterministica).

Ipotesi B (decisa con l'utente): il "prodotto" e' la riga di
`estrazione.righe_listino` — ha gia' `codice_normalizzato` (chiave verso i prezzi,
da `codes.canonical`). La cucitura "visiva" NON e' un JOIN su codice (Qdrant non
ha i codici): e' un LINK DETERMINISTICO riga-prezzo <-> pagina Qdrant sulla chiave
naturale `(doc_stem, page)`. Niente GraphDB, niente nuova estrazione.

Allineamento chiave (punto 6): entrambi i mondi numerano le pagine 1-indexed
(`enumerate(doc, start=1)` in extraction/pipeline.py e pixelrag/ingest.py) e usano
`doc_stem` (Qdrant lo espone sotto la chiave payload `doc`). `(doc_stem, page)` e'
quindi allineato per costruzione; il link e' su chiave ESATTA (off-by-one = orfano).

Fail-Closed assoluto: una riga la cui pagina visiva e' assente — o non visibile al
`role` (RBAC asimmetrico, come Fase 1.3) — NON viene mai associata a indovinare.
Finisce negli scarti di *merge*, concettualmente distinti dagli scarti di
*estrazione* 1.2. Ogni associazione automatica ha evidenza (pagina visiva presente).

Ports & Adapters (come 1.1/1.2/1.3): `VisualCatalog` e' il Port (lookup
deterministico RBAC-aware per `doc_stem`); `FakeVisualCatalog` gira in memoria
(DB-free) nei test. L'adapter reale su Qdrant `aike_pages_visual` (scroll per
payload) e il reader read-only di `righe_listino` arrivano in un Passo successivo.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from src.common.roles import Role
from src.ingestion.extraction.schema import RigaListino
from src.retrieval.pixelrag.index import DEFAULT_VISUAL_COLLECTION


class MotivoScartoMerge(str, Enum):
    """Perche' una riga-prezzo non e' stata associata (audit dashboard Fase 2.2).

    YAGNI: si aggiungono i motivi solo quando un test li richiede (MATCH_AMBIGUO,
    PREZZO_MANCANTE/ANOMALO arriveranno con i rispettivi RED)."""

    # Nessuna pagina visiva per (doc_stem, page) visibile al ruolo. RBAC asimmetrico:
    # "invisibile al ruolo" e' trattato come "assente" (fail-closed, niente leak).
    PAGINA_VISIVA_ASSENTE = "pagina_visiva_assente"


@dataclass(frozen=True)
class Associazione:
    """Cucitura riuscita: riga-prezzo <-> pagina visiva `(doc_stem, page)`.

    Il link di verifica visiva e' la coppia `(doc_stem, page)` stessa (PDF sorgente
    + pagina): la proiezione di ricerca su Qdrant `aike_pages_visual` sta lo'."""

    doc_stem: str
    page: int
    codice_normalizzato: str
    codice_originale: str


@dataclass(frozen=True)
class ScartoMerge:
    """Riga incerta/orfana da rivedere a mano ("Forza Associazione" in dashboard).

    Porta il codice originale + normalizzato tentato e il link di verifica
    `(doc_stem, page)` (PDF sorgente), come da piano §2.2."""

    doc_stem: str
    page: int
    codice_originale: str
    codice_normalizzato_tentato: str
    motivo: MotivoScartoMerge


@dataclass(frozen=True)
class MergeResult:
    """Esito del merge: cuciture riuscite + scarti da verificare."""

    associazioni: list[Associazione] = field(default_factory=list)
    scarti: list[ScartoMerge] = field(default_factory=list)


@runtime_checkable
class VisualCatalog(Protocol):
    """Port del catalogo visivo: quali pagine di un documento sono presenti e
    visibili a un ruolo. RBAC fail-closed dentro il Port (mai default aperto).

    Reale = Qdrant `aike_pages_visual` (scroll per payload `doc`/`ruoli_consentiti`);
    Fake = in memoria per i test."""

    def pages_for(self, doc_stem: str, *, role: Role) -> set[int]:
        ...


@dataclass
class FakeVisualCatalog:
    """Catalogo visivo in memoria per i test (DB-free).

    `pages`: lista di `(doc_stem, page, ruoli_consentiti)`. `pages_for` filtra
    fail-closed sul ruolo — una pagina torna solo se il ruolo e' fra i consentiti,
    riproducendo l'RBAC asimmetrico del reale (Qdrant `ruoli_consentiti`)."""

    pages: list[tuple[str, int, set[Role]]] = field(default_factory=list)

    def pages_for(self, doc_stem: str, *, role: Role) -> set[int]:
        return {
            page
            for stem, page, ruoli in self.pages
            if stem == doc_stem and role in ruoli
        }


class QdrantVisualCatalog:
    """Adapter reale `VisualCatalog` su Qdrant `aike_pages_visual` (Fase 2.1).

    `pages_for` NON e' una vector-search: e' uno **scroll** per payload che raccoglie
    i numeri di pagina distinti del documento visibili al ruolo. Client e collection
    INIETTATI (come `QdrantVisualIndex`/`PgSqlExecutor`): testabile su collection
    usa-e-getta. Import di qdrant **lazy** (il modulo resta importabile senza qdrant).

    RBAC non-divergente (DRY): il dict di sicurezza e' quello di `access_filter` — la
    stessa sorgente del retrieval — tradotto con lo STESSO `MatchValue` di
    `QdrantVisualIndex._to_filter` (su campo-lista Qdrant fa il "contiene"). Fail-closed:
    nessun default apre l'accesso; se il ruolo non compare, la pagina non entra."""

    def __init__(self, client, collection: str = DEFAULT_VISUAL_COLLECTION,
                 scroll_batch: int = 256):
        self._client = client
        self._collection = collection
        self._scroll_batch = scroll_batch

    def pages_for(self, doc_stem: str, *, role: Role) -> set[int]:
        from qdrant_client import models

        from src.retrieval.pixelrag.search import access_filter

        # doc-scoping + RBAC/versione dal medesimo dict del retrieval; traduzione
        # identica a QdrantVisualIndex._to_filter (MatchValue, "contiene" su lista).
        flt = {**access_filter(role), "doc": doc_stem}
        must = [models.FieldCondition(key=key, match=models.MatchValue(value=str(val)))
                for key, val in flt.items() if val is not None]
        qfilter = models.Filter(must=must)

        pages: set[int] = set()
        offset = None
        while True:  # scroll loop: cicla su next_page_offset, mai troncare -> no falsi orfani
            points, offset = self._client.scroll(
                self._collection, scroll_filter=qfilter, limit=self._scroll_batch,
                offset=offset, with_payload=True, with_vectors=False)
            for p in points:
                raw = (p.payload or {}).get("page")
                try:
                    pages.add(int(raw))
                except (TypeError, ValueError):
                    continue  # dato sporco/mancante: fail-closed, pagina non esposta
            if offset is None:
                break
        return pages


def merge(rows: Iterable[RigaListino], catalog: VisualCatalog, *, role: Role) -> MergeResult:
    """Cuce ogni riga-prezzo alla sua pagina visiva `(doc_stem, page)`.

    Per ogni riga: se `page` e' fra le pagine del documento visibili al `role`,
    e' un'associazione; altrimenti fail-closed -> scarto (`PAGINA_VISIVA_ASSENTE`),
    mai un'associazione a indovinare.

    Anti-N+1: le migliaia di righe condividono pochi `doc_stem`. `pages_for` viene
    interrogato UNA volta per `doc_stem` e il set risultante e' messo in cache
    locale (sicura: l'intero merge gira sotto un solo `role`)."""
    associazioni: list[Associazione] = []
    scarti: list[ScartoMerge] = []
    cache: dict[str, set[int]] = {}

    for r in rows:
        pagine = cache.get(r.doc_stem)
        if pagine is None:
            pagine = catalog.pages_for(r.doc_stem, role=role)
            cache[r.doc_stem] = pagine

        if r.page in pagine:
            associazioni.append(Associazione(
                doc_stem=r.doc_stem,
                page=r.page,
                codice_normalizzato=r.codice_normalizzato,
                codice_originale=r.codice_originale,
            ))
        else:
            scarti.append(ScartoMerge(
                doc_stem=r.doc_stem,
                page=r.page,
                codice_originale=r.codice_originale,
                codice_normalizzato_tentato=r.codice_normalizzato,
                motivo=MotivoScartoMerge.PAGINA_VISIVA_ASSENTE,
            ))

    return MergeResult(associazioni=associazioni, scarti=scarti)
