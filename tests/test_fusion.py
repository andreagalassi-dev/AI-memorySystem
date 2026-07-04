"""P3.3 — Test dell'accordo testo↔visione (matching (doc,page) puro) e della
promozione MEDIA→ALTA nella matrice di confidenza.

Vincolo sotto test: la promozione lifta SOLO la banda MEDIA e mai il near-tie;
i test P3.2 restano invariati (garanzia additiva). δ e topk iniettati.
"""

from __future__ import annotations

from src.retrieval.confidence import Level, Thresholds, confidence_level
from src.retrieval.fusion import page_agreement
from tests.test_confidence import sig

T = Thresholds(tau_hi=0.5, tau_lo=0.2)


# ------------------------------------------------------------- page_agreement

class TestAccordo:
    def test_agree_pagina_esatta(self):
        assert page_agreement(("rotax_912", 22),
                              [("rotax_912", 22)], delta=0) == "agree"

    def test_agree_entro_delta(self):
        assert page_agreement(("rotax_912", 22),
                              [("rotax_912", 23)], delta=1) == "agree"

    def test_no_agree_oltre_delta(self):
        # stesso doc ma pagine lontane -> None (debole), non agree
        assert page_agreement(("rotax_912", 22),
                              [("rotax_912", 40)], delta=1) is None

    def test_disagree_doc_diverso(self):
        assert page_agreement(("rotax_912", 22),
                              [("kohler_tp7120", 26)], delta=1) == "disagree"

    def test_none_visivo_vuoto(self):
        assert page_agreement(("rotax_912", 22), [], delta=1) is None

    def test_none_testo_assente(self):
        assert page_agreement(None, [("rotax_912", 22)], delta=1) is None

    def test_agree_dentro_topk(self):
        # match al 3° posto visivo, topk=3 -> agree
        vis = [("altro_A", 1), ("altro_B", 2), ("rotax_912", 22)]
        assert page_agreement(("rotax_912", 22), vis, delta=0, topk=3) == "agree"

    def test_no_agree_oltre_topk(self):
        # match al 4° posto ma topk=3 -> non conta; top-1 doc diverso -> disagree
        vis = [("altro_A", 1), ("altro_B", 2), ("altro_C", 3), ("rotax_912", 22)]
        assert page_agreement(("rotax_912", 22), vis, delta=0, topk=3) == "disagree"


# ------------- promozione NON adottata (misurata non sicura, docs/p33-*.md)

class TestPromozioneNonAdottata:
    def test_banda_media_con_agree_NON_promuove(self):
        # DECISIONE P3.3: l'accordo non promuove (caso W014: doppio-errore stesso
        # doc -> falso-ALTA). Resta MEDIA per proteggere ALTA precision 1.0.
        assert confidence_level(sig(margin=0.3, agreement="agree"), T).level == Level.MEDIA

    def test_banda_media_disagree_resta_media(self):
        assert confidence_level(sig(margin=0.3, agreement="disagree"), T).level == Level.MEDIA

    def test_disagree_non_abbassa_ma_blocca_alta(self):
        # disagree su margine decisivo: niente ALTA (P3.2), scende a MEDIA
        assert confidence_level(sig(margin=0.9, agreement="disagree"), T).level == Level.MEDIA

    def test_margine_decisivo_resta_alta(self):
        assert confidence_level(sig(margin=0.9, agreement=None), T).level == Level.ALTA
