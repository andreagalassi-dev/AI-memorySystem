"""P3.2 — Motore "non lo so": segnali oggettivi -> livelli di sicurezza.

Assegna ALTA / MEDIA / BASSA a una risposta usando SOLO segnali deterministici
del retrieval (SPEC §F.2): gate del guardrail (P3.1), presenza evidenza,
margine relativo top1-top2, evidenza OCR/degraded, e (da P3.3) accordo
testo-visione. MAI l'auto-confidenza dell'LLM. Design: docs/superpowers/specs/
2026-07-03-p32-nonloso-signals-design.md.

Regola d'oro: giusto oppure "non lo so", mai sbagliato in silenzio.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence


class Level(Enum):
    ALTA = "ALTA"
    MEDIA = "MEDIA"
    BASSA = "BASSA"


@dataclass
class Thresholds:
    """Soglie del margine, fittate per-scorer sull'eval (design §7)."""
    tau_hi: float
    tau_lo: float


# Soglie calibrate su eval v1 (scripts/calibrate_confidence.py, 2026-07-04).
# PROVVISORIE: il canale reranker NON e' separabile sul solo margine (POS_HIT1 e
# POS_MISS si sovrappongono; 13/34 POS_MISS sono confident-wrong). tau_hi=0.90 e'
# sopra il ceiling dei POS_MISS -> ALTA precision 1.0 (zero falsi-ALTA); tau_lo
# basso -> BASSA-su-margine solo per retrieval indeciso, il resto -> MEDIA. Il
# silenzio dei confident-wrong naturali e' rimandato a P3.3 (accordo testo-
# visione). Da ri-fittare col set validato dal tecnico. Report: docs/p32-*.md.
CALIBRATED_V1 = Thresholds(tau_hi=0.90, tau_lo=0.0054)


@dataclass
class Signals:
    gate: str                    # "PASS" | "VERIFY" | "BLOCK" (da P3.1)
    has_evidence: bool
    margin: float                # [0,1], relativo top1-top2
    unreliable: bool             # top hit ocr_source / status=degraded
    agreement: str | None = None  # "agree" | "disagree" | None (P3.3)


@dataclass
class ConfidenceReport:
    level: Level
    signals: Signals
    reason: str


def compute_margin(scores: Sequence[float]) -> float:
    """Margine relativo (s1-s2)/s1 sui punteggi (non-negativi) del canale.

    Edge case (design §3): 0 hit -> 0.0; 1 solo hit -> 1.0 (max separazione);
    s1<=0 -> 0.0 (es. BM25 top 0 sui code inesistenti); clamp in [0,1].
    Ordina internamente: non assume l'ordine del chiamante.
    """
    xs = sorted(scores, reverse=True)
    if not xs:
        return 0.0
    s1 = xs[0]
    if s1 <= 0:
        return 0.0
    if len(xs) == 1:
        return 1.0
    return max(0.0, min(1.0, (s1 - xs[1]) / s1))


def confidence_level(signals: Signals, thresholds: Thresholds) -> ConfidenceReport:
    """Matrice di calibrazione (design §5), fail-closed, valutata dall'alto."""
    s, t = signals, thresholds

    # Hard gate 1 -> BASSA ("non lo so")
    if s.gate == "BLOCK":
        return ConfidenceReport(Level.BASSA, s,
                                "guardrail ha BLOCCATO un codice -> non lo so")
    if not s.has_evidence:
        return ConfidenceReport(Level.BASSA, s,
                                "nessuna evidenza recuperata -> non lo so")

    # Hard gate 2 -> MEDIA ("verifica consigliata")
    if s.gate == "VERIFY":
        return ConfidenceReport(Level.MEDIA, s,
                                "guardrail a VERIFICA MANUALE -> verifica consigliata")
    if s.unreliable:
        return ConfidenceReport(Level.MEDIA, s,
                                "evidenza inaffidabile (OCR/degraded) -> verifica consigliata")

    # Soft score (gate PASS, evidenza presente, evidenza affidabile)
    if s.margin >= t.tau_hi and s.agreement != "disagree":
        return ConfidenceReport(Level.ALTA, s,
                                f"margine {s.margin:.2f} >= tau_hi {t.tau_hi} e "
                                "nessun disaccordo visivo -> alta")
    # NB: la promozione cross-modale MEDIA->ALTA e' stata MISURATA non sicura in
    # P3.3 (docs/p33-fusion-calibration.md): l'accordo testo-visione non
    # distingue una doppia-conferma corretta da un doppio-errore sullo stesso
    # doc (caso W014) -> falso-ALTA a ogni delta. NON adottata: l'accordo resta
    # diagnostico, non promuove. ALTA precision 1.0 preservata.
    if s.margin >= t.tau_lo or s.agreement == "agree":
        why = (f"margine {s.margin:.2f} >= tau_lo {t.tau_lo}"
               if s.margin >= t.tau_lo else "accordo testo-visione")
        return ConfidenceReport(Level.MEDIA, s, f"{why} -> verifica consigliata")
    return ConfidenceReport(Level.BASSA, s,
                            f"margine {s.margin:.2f} < tau_lo {t.tau_lo} senza "
                            "accordo visivo -> non lo so")


def extract_signals(unified: dict, guardrail_report) -> Signals:
    """Deriva i segnali oggettivi da un risultato search_unified + il report
    del guardrail. Nessun LLM. `agreement` resta None in P3.2 (P3.3)."""
    hits = unified.get("text_hits") or []
    is_code = unified.get("route") == "code"
    scores = [h.score for h in hits]
    top = hits[0] if hits else None

    if is_code:
        has_evidence = bool(hits) and hits[0].score > 0
    else:
        has_evidence = bool(hits)

    unreliable = False
    if top is not None:
        pl = getattr(top, "payload", None) or {}
        unreliable = (pl.get("status") == "degraded"
                      or "ocr_source" in (pl.get("warnings") or []))

    return Signals(
        gate=guardrail_report.gate,
        has_evidence=has_evidence,
        margin=compute_margin(scores),
        unreliable=unreliable,
        agreement=None,
    )
