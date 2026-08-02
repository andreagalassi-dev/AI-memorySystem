"""P3.2 — Test del motore "non lo so" (segnali oggettivi -> livelli).

Vincolo sotto test (SPEC §F.2): l'incertezza NON usa l'auto-confidenza
dell'LLM, solo segnali deterministici del retrieval. La matrice di calibrazione
(design §5) e' verificata riga-per-riga con soglie iniettate note.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.retrieval.confidence import (
    Level,
    Signals,
    Thresholds,
    compute_margin,
    confidence_level,
    extract_signals,
    group_scores,
)

T = Thresholds(tau_hi=0.5, tau_lo=0.2)


def sig(gate="PASS", has_evidence=True, margin=0.9, unreliable=False,
        agreement=None, is_generic_code=False, singleton=False, is_code=False):
    return Signals(gate=gate, has_evidence=has_evidence, margin=margin,
                   unreliable=unreliable, agreement=agreement,
                   is_generic_code=is_generic_code, singleton=singleton, is_code=is_code)


def level(**kw):
    return confidence_level(sig(**kw), T).level


# ------------------------------------------------------------- compute_margin

class TestMargine:
    def test_zero_hit(self):
        assert compute_margin([]) == 0.0

    def test_un_solo_hit_forza_uno(self):  # edge case approvato
        assert compute_margin([7.3]) == 1.0

    def test_due_hit_relativo(self):
        assert compute_margin([10.0, 4.0]) == 0.6

    def test_ordina_internamente(self):
        assert compute_margin([4.0, 10.0]) == 0.6

    def test_s1_non_positivo_azzera(self):  # BM25 top 0 sui code inesistenti
        assert compute_margin([0.0, 0.0]) == 0.0
        assert compute_margin([-1.0, -2.0]) == 0.0

    def test_clamp(self):
        # s2 negativo -> margine > 1 -> clamp a 1.0
        assert compute_margin([5.0, -5.0]) == 1.0


# ------------------------------------- group-by codice (debito §3, duplicati)

class TestGroupScores:
    def test_duplicati_stesso_codice_fusi(self):
        # caso 0USBX4: righe bilingui a score identico -> 1 gruppo -> margine 1.0
        gs = group_scores([("0USBX4", 15.925), ("0USBX4", 15.925)])
        assert gs == [15.925]
        assert compute_margin(gs) == 1.0

    def test_max_per_gruppo(self):
        gs = group_scores([("A1", 10.0), ("A1", 4.0), ("B2", 8.0)])
        assert sorted(gs, reverse=True) == [10.0, 8.0]

    def test_codici_diversi_non_fusi(self):
        # l'ambiguita' VERA (due codici distinti a pari score) resta misurata
        gs = group_scores([("A1", 10.0), ("B2", 10.0)])
        assert compute_margin(gs) == 0.0

    def test_senza_codice_gruppi_singleton(self):
        # due no-code identici: margine 0 come oggi (rifiuto conservativo)
        gs = group_scores([(None, 5.0), (None, 5.0)])
        assert len(gs) == 2 and compute_margin(gs) == 0.0

    def test_misto_code_e_nocode(self):
        gs = group_scores([("A1", 10.0), (None, 6.0), ("A1", 9.0), (None, 6.0)])
        assert sorted(gs, reverse=True) == [10.0, 6.0, 6.0]

    def test_codice_canonico_unifica_le_forme(self):
        # stessa definizione del guardrail: 'V0 9156' e 'v09156' sono lo stesso
        gs = group_scores([("V0 9156", 7.0), ("v09156", 7.0)])
        assert gs == [7.0]

    def test_vuoto(self):
        assert group_scores([]) == []


# ------------------------------------------------- confidence_level (matrice §5)

class TestMatriceHardGates:
    def test_block_sempre_bassa(self):  # riga 1
        assert level(gate="BLOCK", margin=0.99) == Level.BASSA

    def test_nessuna_evidenza_bassa(self):  # riga 2
        assert level(has_evidence=False, margin=0.99) == Level.BASSA
        assert level(gate="VERIFY", has_evidence=False) == Level.BASSA

    def test_verify_con_evidenza_media(self):  # riga 3
        assert level(gate="VERIFY", margin=0.99) == Level.MEDIA

    def test_ocr_inaffidabile_media(self):  # riga 4
        assert level(unreliable=True, margin=0.99) == Level.MEDIA

    def test_generic_code_media(self):
        assert level(is_generic_code=True, margin=0.99) == Level.MEDIA


class TestMatriceSoftScore:
    def test_alta(self):  # riga 5: margine alto, no disagree
        assert level(margin=0.6, agreement=None) == Level.ALTA
        assert level(margin=0.6, agreement="agree") == Level.ALTA

    def test_alta_declassata_da_disagree(self):  # riga 6
        assert level(margin=0.6, agreement="disagree") == Level.MEDIA

    def test_lone_hit_narrative_media(self):
        # lone-hit narrativo (singleton=True, is_code=False, margin 1.0) -> MEDIA
        assert level(margin=1.0, singleton=True, is_code=False) == Level.MEDIA

    def test_lone_hit_code_alta(self):
        # lone-hit CODE (singleton=True, is_code=True) -> resta ALTA (se margine alto)
        assert level(margin=1.0, singleton=True, is_code=True) == Level.ALTA
        
    def test_multi_hit_narrative_alta(self):
        # multi-hit con separazione (margin>=tau_hi, singleton=False) -> ALTA invariato
        assert level(margin=1.0, singleton=False, is_code=False) == Level.ALTA

    def test_margine_medio(self):  # riga 7
        assert level(margin=0.3) == Level.MEDIA

    def test_margine_basso_ma_accordo_visivo(self):  # riga 8
        assert level(margin=0.1, agreement="agree") == Level.MEDIA

    def test_margine_basso_senza_accordo_bassa(self):  # riga 9
        assert level(margin=0.1, agreement=None) == Level.BASSA
        assert level(margin=0.1, agreement="disagree") == Level.BASSA


class TestProprietaGlobali:
    def test_monotonia_margine(self):
        # a parita' di resto, margine crescente non abbassa il livello
        order = {Level.BASSA: 0, Level.MEDIA: 1, Level.ALTA: 2}
        vals = [order[level(margin=m)] for m in (0.0, 0.1, 0.3, 0.6, 0.9)]
        assert vals == sorted(vals)

    def test_A_none_non_promuove_da_sola(self):
        # con margine sotto tau_lo, A=None resta BASSA (nessuna promozione)
        assert level(margin=0.1, agreement=None) == Level.BASSA

    def test_reason_sempre_presente(self):
        for kw in [{"gate": "BLOCK"}, {"unreliable": True}, {"margin": 0.1}]:
            assert confidence_level(sig(**kw), T).reason.strip()

    def test_soglie_calibrate_v1_sane(self):
        from src.retrieval.confidence import CALIBRATED_V1
        assert 0 <= CALIBRATED_V1.tau_lo < CALIBRATED_V1.tau_hi <= 1


# --------------------------------------------------------------- extract_signals

@dataclass
class FakeHit:
    score: float
    payload: dict


@dataclass
class FakeReport:
    gate: str


class TestEstrazioneSegnali:
    def test_code_evidenza_da_bm25(self):
        unified = {"route": "code", "text_hits": [FakeHit(11.2, {}),
                                                  FakeHit(4.0, {})]}
        s = extract_signals(unified, FakeReport("PASS"))
        assert s.has_evidence and s.gate == "PASS"
        assert abs(s.margin - (11.2 - 4.0) / 11.2) < 1e-9

    def test_code_bm25_zero_niente_evidenza(self):
        unified = {"route": "code", "text_hits": [FakeHit(0.0, {})]}
        s = extract_signals(unified, FakeReport("PASS"))
        assert not s.has_evidence

    def test_natural_evidenza_da_presenza(self):
        unified = {"route": "natural", "text_hits": [FakeHit(0.9, {})]}
        assert extract_signals(unified, FakeReport("PASS")).has_evidence

    def test_ocr_source_marca_inaffidabile(self):
        unified = {"route": "natural",
                   "text_hits": [FakeHit(0.9, {"warnings": ["ocr_source"]})]}
        assert extract_signals(unified, FakeReport("PASS")).unreliable

    def test_status_degraded_marca_inaffidabile(self):
        unified = {"route": "natural",
                   "text_hits": [FakeHit(0.9, {"status": "degraded"})]}
        assert extract_signals(unified, FakeReport("PASS")).unreliable

    def test_agreement_none_in_p32(self):
        unified = {"route": "natural", "text_hits": [FakeHit(0.9, {})]}
        assert extract_signals(unified, FakeReport("PASS")).agreement is None

    def test_generic_code_signal(self):
        unified = {"route": "natural",
                   "text_hits": [FakeHit(0.9, {"code_normalized_by": "generic"})]}
        assert extract_signals(unified, FakeReport("PASS")).is_generic_code is True
