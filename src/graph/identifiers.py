"""Epica F.2a-2 — nozione UNICA di "identificatore" (codice/part-number).

Estratta VERBATIM da `contextualize.py` (dov'era `_identifiers`) perche' ora
serve a DUE moduli: la guardia VERBATIM M1 della riformulazione
(`contextualize.py`) e la politica di autorizzazione della memoria
(`memory_policy.py`). Due copie della stessa nozione divergerebbero, e una
divergenza qui e' esattamente il buco da cui passa un codice sbagliato.

Comportamento INVARIATO rispetto a `contextualize._identifiers`.
"""

from __future__ import annotations

# SOLA LETTURA dal router deterministico (non-LLM): stesso concetto di
# "identificatore" (token con cifre) usato per riconoscere i codici. NON lo
# modifichiamo — riusiamo il suo regex per coerenza tra router, guardia M1 e
# politica di memoria.
from src.retrieval.router import TOKEN_RE, _HAS_DIGIT


def identifiers(text: str) -> list[str]:
    """Gli 'identificatori' di un testo: token con cifre, len>=2, senza virgole
    (i prezzi hanno la virgola). Stesso criterio del router (`_codey`)."""
    out = []
    for t in TOKEN_RE.findall(text or ""):
        if len(t) >= 2 and bool(_HAS_DIGIT.search(t)) and "," not in t:
            out.append(t)
    return out
