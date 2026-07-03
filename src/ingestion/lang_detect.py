"""P2.6 — Rilevatore di lingua deterministico a stopword (IT/EN/DE/ES/FR).

Niente dipendenze ML: conteggio di stopword ad alta frequenza sul testo
estratto, per-documento. Serve alle metriche per lingua (SPEC §F.1). Con testo
scarso o punteggi pari torna 'und' (undetermined) — mai indovinare.
"""

from __future__ import annotations

import re
from collections import Counter

STOPWORDS = {
    "IT": {"il", "la", "di", "che", "per", "con", "del", "della", "una", "sono",
           "non", "gli", "le", "dei", "nel", "alla", "come", "anche", "questo"},
    "EN": {"the", "of", "and", "to", "for", "with", "that", "this", "from",
           "are", "not", "which", "must", "should", "when", "before"},
    "DE": {"der", "die", "das", "und", "mit", "für", "von", "nicht", "eine",
           "auf", "werden", "sind", "bei", "nach", "oder", "durch"},
    "ES": {"el", "los", "las", "por", "para", "con", "una", "del", "que",
           "este", "esta", "son", "más", "sin", "sobre", "cuando"},
    "FR": {"le", "les", "des", "pour", "avec", "dans", "est", "une", "sur",
           "pas", "aux", "ces", "être", "sont", "cette", "vous"},
}
_WORD = re.compile(r"[a-zà-ÿ]+")
MIN_HITS = 5


def detect_lang(text: str) -> str:
    words = Counter(_WORD.findall(text.lower()))
    scores = {lang: sum(words[w] for w in sw) for lang, sw in STOPWORDS.items()}
    best = max(scores, key=lambda k: scores[k])
    ordered = sorted(scores.values(), reverse=True)
    if ordered[0] < MIN_HITS or (len(ordered) > 1 and ordered[0] == ordered[1]):
        return "und"
    return best
