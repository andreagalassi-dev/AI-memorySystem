"""Epica C.3 — batching dell'upsert visivo (fix timeout Qdrant su doc giganti).

DB-free: un client fake registra ogni chiamata upsert. Verifica che un upsert
di molti punti venga spezzato in blocchi <= VISUAL_UPSERT_BATCH (un unico upsert
di ~1000 pagine multivettore supera il timeout Qdrant a 5s, osservato su
era_macchine_rotanti_catalogo, 998 pagine)."""

from __future__ import annotations

from src.retrieval.pixelrag.index import (
    VISUAL_UPSERT_BATCH,
    QdrantVisualIndex,
    VisualPoint,
)


class _FakeClient:
    """Registra i punti di ogni chiamata upsert (nessuna rete)."""

    def __init__(self) -> None:
        self.calls: list[int] = []  # numero di punti per chiamata

    def upsert(self, collection, points):  # firma compatibile
        self.calls.append(len(points))


def _points(n: int) -> list[VisualPoint]:
    return [
        VisualPoint(
            point_id=str(i), doc="d", page=i, kind="thumb", row=0, col=0,
            vector=[0.1, 0.2], payload={"doc": "d"},
        )
        for i in range(n)
    ]


def test_upsert_spezza_in_blocchi():
    fake = _FakeClient()
    idx = QdrantVisualIndex(fake, collection="c")
    n = VISUAL_UPSERT_BATCH * 2 + 5  # 2 blocchi pieni + 1 resto
    total = idx.upsert(_points(n))
    assert total == n
    # 3 chiamate: due piene + il resto
    assert fake.calls == [VISUAL_UPSERT_BATCH, VISUAL_UPSERT_BATCH, 5]
    # nessun blocco supera la soglia
    assert all(c <= VISUAL_UPSERT_BATCH for c in fake.calls)


def test_upsert_singolo_blocco():
    fake = _FakeClient()
    idx = QdrantVisualIndex(fake, collection="c")
    total = idx.upsert(_points(10))
    assert total == 10
    assert fake.calls == [10]


def test_upsert_vuoto_nessuna_chiamata():
    fake = _FakeClient()
    idx = QdrantVisualIndex(fake, collection="c")
    assert idx.upsert([]) == 0
    assert fake.calls == []
