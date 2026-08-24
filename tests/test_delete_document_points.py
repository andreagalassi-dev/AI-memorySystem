"""La cancellazione di un documento deve trovarlo davvero.

Difetto reale, misurato sull'indice vivo il 24/08: `delete_document_points`
filtrava sulla sola chiave `doc_stem`, che pero' **0 punti su 10385** hanno nel
ramo visivo e solo 166474 su 199447 nel narrativo. Cioe' sul visivo era un no-op
silenzioso al 100%, e sul narrativo lasciava indietro il 16.5% dei punti.

Il test finto NON si limita a guardare la forma del filtro: **lo applica** a un
corpus con le due generazioni di payload, cosi' misura il comportamento e non
l'intenzione. E' la stessa disciplina di `tests/test_graph_hop2_rbac.py`.
"""

from __future__ import annotations

import pytest
from qdrant_client import models

from src.ingestion.ingest_one import delete_document_points

# due generazioni di payload, entrambe presenti nell'indice reale:
#   "vecchia" -> solo `doc` (tutti i punti visivi, e 32973 narrativi)
#   "nuova"   -> `doc` + `doc_stem`
CORPUS = [
    {"id": 1, "doc": "alfa"},                        # vecchia generazione
    {"id": 2, "doc": "alfa", "doc_stem": "alfa"},    # nuova generazione
    {"id": 3, "doc": "beta"},                        # altro documento
    {"id": 4, "doc": "beta", "doc_stem": "beta"},    # altro documento
]


def _match(punto: dict, cond) -> bool:
    return punto.get(cond.key) == cond.match.value


def _applica(flt: models.Filter, corpus: list[dict]) -> list[dict]:
    """Semantica Qdrant ridotta a cio' che serve: `must` = AND, `should` = OR."""
    out = []
    for p in corpus:
        ok_must = all(_match(p, c) for c in (flt.must or []))
        ok_should = (not flt.should) or any(_match(p, c) for c in flt.should)
        if ok_must and ok_should:
            out.append(p)
    return out


class FakeQdrant:
    """Applica DAVVERO il filtro ricevuto: misura il comportamento."""

    def __init__(self, corpus):
        self.corpus = list(corpus)
        self.cancellati: list[dict] = []

    def count(self, collection, exact=True, count_filter=None):
        n = len(_applica(count_filter, self.corpus)) if count_filter else len(self.corpus)
        return type("R", (), {"count": n})()

    def delete(self, collection, points_selector=None, wait=True):
        colpiti = _applica(points_selector.filter, self.corpus)
        self.cancellati = colpiti
        self.corpus = [p for p in self.corpus if p not in colpiti]


class TestCancellazioneDocumento:
    def test_prende_anche_i_punti_senza_doc_stem(self):
        """Il caso reale: sul visivo NESSUN punto ha `doc_stem`."""
        qc = FakeQdrant([{"id": 9, "doc": "alfa"}])
        n = delete_document_points(qc, "aike_pages_visual", "alfa")
        assert n == 1, "un punto con solo `doc` deve essere trovato e cancellato"
        assert qc.corpus == []

    def test_prende_entrambe_le_generazioni(self):
        qc = FakeQdrant(CORPUS)
        n = delete_document_points(qc, "aike_chunks_v2", "alfa")
        assert n == 2
        assert {p["id"] for p in qc.cancellati} == {1, 2}

    def test_non_tocca_gli_altri_documenti(self):
        """La condizione e' un OR fra due chiavi, non fra due documenti."""
        qc = FakeQdrant(CORPUS)
        delete_document_points(qc, "aike_chunks_v2", "alfa")
        assert {p["id"] for p in qc.corpus} == {3, 4}

    @pytest.mark.parametrize("stem", ["alf", "alfa2", "ALFA", ""])
    def test_quasi_giusti_non_vengono_cancellati(self, stem):
        """Corrispondenza esatta: un nome quasi uguale e' un altro documento."""
        qc = FakeQdrant(CORPUS)
        assert delete_document_points(qc, "aike_chunks_v2", stem) == 0
        assert len(qc.corpus) == 4

    def test_conta_prima_di_cancellare(self):
        """Il valore ritornato e' quanti punti c'erano, non quanti ne restano."""
        qc = FakeQdrant(CORPUS)
        assert delete_document_points(qc, "aike_chunks_v2", "beta") == 2
