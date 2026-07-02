"""P2.5 — Test del classificatore di pagine.

Unit: le regole sui 5 casi-trappola misurati sul corpus + casi netti.
Integrazione (skip senza corpus/): accuratezza binaria sul campione etichettato
a mano (dev+holdout), con soglia minima come da DoD.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import pytest

from src.ingestion.page_classifier import classify

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "data" / "page_labels" / "labels_v0.csv"


def f(chars=0, n_images=0, img=0.0, draw=0):
    return {"chars": chars, "n_images": n_images,
            "img_area_ratio": img, "n_drawings": draw}


class TestRegole:
    def test_tabella_pura(self):
        # pi_pulley: griglia tabella = centinaia di drawings ma testo ricco
        pc = classify(f(chars=3586, draw=208))
        assert (pc.label, pc.dual) == ("testuale", False)

    def test_esploso_vettoriale(self):
        # kohler_industrial: 26k drawing paths, 123 chars
        pc = classify(f(chars=123, draw=26572))
        assert (pc.label, pc.dual) == ("grafica", False)

    def test_esploso_raster(self):
        # branson: 1131 immagini, 57% area, 65 chars
        pc = classify(f(chars=65, n_images=1131, img=0.57))
        assert pc.label == "grafica"

    def test_trappola_scansione_con_text_layer(self):
        # lycoming: 100% immagine MA 2038 chars estraibili -> TESTUALE
        pc = classify(f(chars=2038, n_images=1, img=1.0))
        assert (pc.label, pc.no_text_layer) == ("testuale", False)

    def test_trappola_mista_dominanza_testo(self):
        # Mamoli p81: griglia figure+tabelle, 2415 chars, 251 draw
        pc = classify(f(chars=2415, n_images=8, img=0.11, draw=251))
        assert pc.label == "testuale"

    def test_trappola_mista_dominanza_grafica_dual(self):
        # gehl p94: wiring con 50k drawings e 1258 chars di etichette
        pc = classify(f(chars=1258, draw=50629))
        assert (pc.label, pc.dual) == ("grafica", True)

    def test_mista_silca_dual(self):
        # Silca p2: tabella ricambi (2235 chars) + esploso (81k drawings)
        pc = classify(f(chars=2235, n_images=39, img=0.033, draw=81670))
        assert pc.dual is True  # in OGNI caso va anche all'altra pipeline

    def test_trappola_scansione_muta(self):
        # kbs ready700: 100% immagine, 0 chars -> corsia OCR
        pc = classify(f(chars=0, n_images=1, img=1.0))
        assert (pc.label, pc.no_text_layer) == ("grafica", True)

    def test_trappola_testo_vettoriale_cad(self):
        # kbs_4055041: tabella con testo-outline (0 chars, 1662 drawings)
        pc = classify(f(chars=0, draw=1662))
        assert (pc.label, pc.no_text_layer) == ("grafica", True)

    def test_copertina_con_foto(self):
        # Mamoli p52: cover di sezione, 68 chars, foto 14% area
        pc = classify(f(chars=68, n_images=1, img=0.1386, draw=14))
        assert pc.label == "grafica"

    def test_pagina_quasi_vuota(self):
        # rotax 'INTENTIONALLY LEFT BLANK'
        pc = classify(f(chars=24))
        assert (pc.label, pc.low_content) == ("testuale", True)

    def test_reason_auditabile(self):
        assert "chars=" in classify(f(chars=5000)).reason


needs_data = pytest.mark.skipif(
    not (ROOT / "corpus").exists() or not LABELS.exists(),
    reason="corpus/ e data/ locali richiesti",
)


@needs_data
class TestAccuratezzaCampione:
    """DoD P2.5: accuratezza binaria >= 0.90 sul campione etichettato a mano.

    Le soglie sono state calibrate SOLO sulla meta' dev (split deterministico
    per strato); l'holdout e' la misura pulita.
    """

    @pytest.fixture(scope="class")
    def valutazione(self):
        import fitz

        from src.ingestion.page_classifier import page_features

        rows = list(csv.DictReader(LABELS.open(encoding="utf-8")))
        pdf_index = {p.stem: p for p in (ROOT / "corpus").rglob("*.pdf")}
        strata = defaultdict(list)
        for i, r in enumerate(rows):
            strata[(r["cat"], r["label"])].append(i)
        dev, hold = set(), set()
        for _, idxs in sorted(strata.items()):
            for j, i in enumerate(idxs):
                (dev if j % 2 == 0 else hold).add(i)
        preds = []
        for r in rows:
            doc = fitz.open(pdf_index[r["doc"]])
            preds.append(classify(page_features(doc[int(r["page"]) - 1])))
            doc.close()
        return rows, preds, dev, hold

    def _acc(self, rows, preds, idxs):
        return sum(preds[i].label == rows[i]["label"] for i in idxs) / len(idxs)

    def test_accuratezza_dev(self, valutazione):
        rows, preds, dev, _ = valutazione
        assert self._acc(rows, preds, dev) >= 0.90

    def test_accuratezza_holdout(self, valutazione):
        rows, preds, _, hold = valutazione
        assert self._acc(rows, preds, hold) >= 0.90

    def test_recall_grafica_holdout(self, valutazione):
        """Nessun esploso perso: le pagine grafiche GT devono restare grafiche
        (recall classe grafica) — e' il costo d'errore piu' alto per P2.7."""
        rows, preds, _, hold = valutazione
        g = [i for i in hold if rows[i]["label"] == "grafica"]
        assert all(preds[i].label == "grafica" for i in g)