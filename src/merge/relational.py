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
