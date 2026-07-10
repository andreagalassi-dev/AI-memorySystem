"""Fase 2.1 — Merge relazionale Catalogo x Listino (RED).

Ipotesi B (decisa con l'utente): il "prodotto" e' la riga di `righe_listino`
(che ha gia' il `codice_normalizzato`, chiave verso i prezzi). La cucitura
"visiva" e' un LINK DETERMINISTICO riga-prezzo <-> pagina Qdrant sulla chiave
naturale `(doc_stem, page)`. Nessun GraphDB, nessuna estrazione nuova.

Allineamento chiave (punto 6, verificato sul codice): entrambi i mondi numerano
le pagine 1-indexed (`enumerate(doc, start=1)` in extraction/pipeline.py e in
pixelrag/ingest.py) e usano `doc_stem` (Qdrant lo salva sotto la chiave `doc`).
Quindi `(doc_stem, page)` e' allineato per costruzione: qui lo blindiamo con un
test di off-by-one.

Fail-Closed assoluto: una riga la cui pagina visiva e' assente / non visibile al
ruolo NON viene mai associata a indovinare -> finisce negli "scarti di merge"
(tabella DISTINTA dagli scarti di estrazione 1.2). Ogni associazione ha evidenza.

Ports & Adapters: `VisualCatalog` e' il Port (lookup deterministico per
`(doc_stem, page)`, RBAC-aware); `FakeVisualCatalog` gira in memoria (DB-free),
l'adapter reale su Qdrant `aike_pages_visual` arriva in un Passo successivo.
"""

from __future__ import annotations

from decimal import Decimal

from src.common.roles import Role
from src.ingestion.extraction.schema import RigaGrezza, RigaListino
from src.merge.relational import (
    Associazione,
    FakeVisualCatalog,
    MotivoScartoMerge,
    merge,
)


def _riga(codice: str, doc_stem: str, page: int, prezzo: str = "10.00") -> RigaListino:
    """Riga di listino con `codice_normalizzato` calcolato dall'invariante reale."""
    return RigaListino.from_grezza(
        RigaGrezza(codice_originale=codice, descrizione="rubinetto", prezzo=Decimal(prezzo)),
        doc_stem=doc_stem,
        page=page,
    )


def test_riga_prezzo_con_pagina_visiva_diventa_associazione():
    """Riga il cui (doc_stem, page) ha una pagina visiva -> 1 associazione, 0 scarti."""
    rows = [_riga("0US-123.A", "mamoli_2021", 3)]
    catalog = FakeVisualCatalog(pages=[("mamoli_2021", 3, {Role.COMMERCIALE})])

    res = merge(rows, catalog, role=Role.COMMERCIALE)

    assert res.scarti == []
    assert res.associazioni == [
        Associazione(
            doc_stem="mamoli_2021",
            page=3,
            codice_normalizzato="0US123A",
            codice_originale="0US-123.A",
        )
    ]


def test_riga_prezzo_orfana_senza_pagina_visiva_va_negli_scarti():
    """Nessuna pagina visiva per (doc_stem, page) -> fail-closed: scarto, mai associata."""
    rows = [_riga("0US-123.A", "mamoli_2021", 3)]
    catalog = FakeVisualCatalog(pages=[])  # catalogo visivo vuoto: la riga e' orfana

    res = merge(rows, catalog, role=Role.COMMERCIALE)

    assert res.associazioni == []
    assert len(res.scarti) == 1
    s = res.scarti[0]
    assert s.doc_stem == "mamoli_2021"
    assert s.page == 3
    assert s.codice_originale == "0US-123.A"
    assert s.codice_normalizzato_tentato == "0US123A"
    assert s.motivo == MotivoScartoMerge.PAGINA_VISIVA_ASSENTE


def test_rbac_fail_closed_pagina_visibile_ad_altro_ruolo_e_orfana():
    """La pagina esiste ma e' riservata a un altro ruolo -> fail-closed: scarto.

    RBAC asimmetrico (come Fase 1.3): dal punto di vista del ruolo la pagina non
    esiste. Mai un'associazione che esporrebbe un dato non autorizzato.
    """
    rows = [_riga("0US-123.A", "mamoli_2021", 3)]
    catalog = FakeVisualCatalog(pages=[("mamoli_2021", 3, {Role.TECNICO})])

    res = merge(rows, catalog, role=Role.COMMERCIALE)

    assert res.associazioni == []
    assert len(res.scarti) == 1
    assert res.scarti[0].motivo == MotivoScartoMerge.PAGINA_VISIVA_ASSENTE


def test_link_esatto_su_page_off_by_one_non_associa():
    """Punto 6: il link e' su (doc_stem, page) ESATTO. Pagina 4 non si lega alla 3."""
    rows = [_riga("0US-123.A", "mamoli_2021", 4)]
    catalog = FakeVisualCatalog(pages=[("mamoli_2021", 3, {Role.COMMERCIALE})])

    res = merge(rows, catalog, role=Role.COMMERCIALE)

    assert res.associazioni == []
    assert len(res.scarti) == 1
    assert res.scarti[0].motivo == MotivoScartoMerge.PAGINA_VISIVA_ASSENTE
