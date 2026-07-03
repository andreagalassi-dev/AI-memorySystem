"""P2.4 — Router deterministico delle query (non-LLM, auditabile).

Problema misurato in P2.3: su query puro-codice l'RRF ibrido ingerisce rumore
dal canale denso (codice esatto nei top-3 ma non stabilmente top-1). In questo
dominio il codice esatto deve vincere sempre: la query riconosciuta come
"codice" va deviata sul canale lessicale puro.

Due livelli di confidenza, regole esplicite (niente pesi opachi):
  Tier A — famiglia certa: token che matcha il REGISTRO di regex di famiglia
           (oggi Mamoli V0+10; estendibile per-brand, stesso concetto dei
           "profili di normalizzazione" della gate decision P1 §5).
  Tier B — marker esplicito ("codice 11282", "part number 0USBX4") oppure query
           composta da un unico token contenente cifre ("11282", "0USBX4").

Classi: 'code' (solo codice ± 1 parola -> sparse-only), 'mixed' (codice dentro
frase naturale -> ibrida), 'natural' (nessun codice -> ibrida).
Questo router e' il seme del nodo router del grafo P3 (SPEC §C).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Registro famiglie-codice ad alta confidenza: dallo STESSO config del
# normalizzatore (config/brands.json, gate decision P1 §5) — una sola fonte
# di verita' tra ingestion e retrieval.
def _family_patterns() -> list[re.Pattern]:
    from src.ingestion.brands import all_token_patterns

    return all_token_patterns()

# Parole-segnale che promuovono il token successivo a codice (IT/EN/ES).
MARKERS = {"codice", "cod", "cod.", "code", "art", "art.", "articolo",
           "part", "pn", "p/n", "ricambio", "referencia", "código", "codigo",
           "number", "numero", "n.", "nr", "nr."}

TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ./*-]*")
_HAS_DIGIT = re.compile(r"\d")


@dataclass
class QueryRoute:
    query_type: str          # code | mixed | natural
    code_token: str | None   # il token-codice individuato (upper), se presente
    reason: str              # spiegazione auditabile della decisione


def _family_match(token: str) -> bool:
    t = token.upper()
    return any(p.fullmatch(t) for p in _family_patterns())


def _codey(token: str) -> bool:
    """Token candidabile a codice dietro marker: contiene cifre, niente virgole
    (prezzi), lunghezza >= 2."""
    return len(token) >= 2 and bool(_HAS_DIGIT.search(token)) and "," not in token


def classify_query(query: str) -> QueryRoute:
    tokens = TOKEN_RE.findall(query)
    if not tokens:
        return QueryRoute("natural", None, "query vuota/senza token")

    code_token: str | None = None
    reason = ""

    # Tier A — famiglia certa, ovunque nella query
    for t in tokens:
        if _family_match(t):
            code_token, reason = t.upper(), f"tier A: '{t}' matcha famiglia registro"
            break

    # Tier B — marker esplicito seguito da token con cifre
    if code_token is None:
        for i, t in enumerate(tokens[:-1]):
            if t.lower().rstrip(".") in {m.rstrip(".") for m in MARKERS} \
                    and _codey(tokens[i + 1]):
                code_token = tokens[i + 1].upper()
                reason = f"tier B: marker '{t}' + token '{tokens[i + 1]}'"
                break

    # Tier B — query di un unico token con cifre ("11282", "0USBX4")
    if code_token is None and len(tokens) == 1 and _codey(tokens[0]):
        code_token = tokens[0].upper()
        reason = "tier B: query a token singolo con cifre"

    if code_token is None:
        return QueryRoute("natural", None, "nessun token-codice riconosciuto")

    # code vs mixed: quante parole "di contenuto" restano oltre codice e marker?
    marker_set = {m.rstrip(".") for m in MARKERS}
    others = [t for t in tokens
              if t.upper() != code_token and t.lower().rstrip(".") not in marker_set]
    if len(others) <= 1:
        return QueryRoute("code", code_token, reason + " -> sparse-only")
    return QueryRoute("mixed", code_token, reason + f" + {len(others)} parole -> ibrida")
