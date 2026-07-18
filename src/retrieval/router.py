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
from enum import StrEnum

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

# Marker di intento VISIVO (P2.7): la query chiede un esploso/schema/disegno
# -> collection visiva aike_pages_visual. Multi-lingua IT/EN/DE/ES.
VISUAL_MARKERS = {"esploso", "esplosa", "esplosi", "spaccato", "schema",
                  "schemi", "tavola", "disegno", "figura", "mostrami",
                  "exploded", "diagram", "drawing", "wiring", "schematic",
                  "explosionszeichnung", "zeichnung", "despiece", "diagrama"}

TOKEN_RE = re.compile(r"[0-9A-Za-zÀ-ÿ][0-9A-Za-zÀ-ÿ./*-]*")
_HAS_DIGIT = re.compile(r"\d")


@dataclass
class QueryRoute:
    query_type: str          # code | mixed | natural
    code_token: str | None   # il token-codice individuato (upper), se presente
    reason: str              # spiegazione auditabile della decisione


class Intent(StrEnum):
    """Fase 1.3 — Intent d'instradamento del Cervello Unificatore (SPEC §C).

    Tipo CHIUSO derivato da `classify_query`: e' l'unico valore che sceglie il ramo
    del grafo. Mai testo libero eseguibile, mai un LLM. StrEnum: il valore e' la
    stringa stessa, serializzabile diretta nello stato del grafo (come `Role`)."""

    STRUCTURED = "structured"   # lookup sul Semantic Layer 1.2 (righe_listino) via SQL tool
    VISUAL = "visual"           # retrieval visivo PixelRAG 1.1
    NARRATIVE = "narrative"     # RAG testuale esistente (default fail-safe)


# Whitelist CHIUSA delle keyword che promuovono una query CON codice a STRUCTURED.
# Fase 1.3: SOLO prezzo (IT/EN/DE). Le keyword di QUANTITA' sono volutamente ESCLUSE:
# `righe_listino` (1.2) non ha colonna quantita', quindi instradare li' una domanda di
# quantita' violerebbe il Fail-Closed ("giusto oppure non lo so"). Si riammetteranno
# quando esistera' la colonna. Distinta dal set campo-detection di `sql_tool`: qui la
# domanda e' "questa query vuole un dato strutturato?", non "quale colonna leggere".
STRUCTURED_KEYWORDS = frozenset({
    "prezzo", "prezzi", "costo", "costa", "costano", "listino", "quanto costa",
    "price", "cost", "how much",
    "preis", "kostet", "kosten",
})


def _wants_structured(query: str) -> bool:
    q = (query or "").lower()
    return any(k in q for k in STRUCTURED_KEYWORDS)


def derive_intent(route: QueryRoute, query: str) -> Intent:
    """Deriva l'intent d'instradamento (deterministico, fail-safe).

    - `visual` -> VISUAL (prevale, come in `classify_query`);
    - codice presente E (route `code` OPPURE keyword-prezzo) -> STRUCTURED;
    - tutto il resto -> NARRATIVE (ramo esistente, gia' presidiato da confidence +
      guardrail): e' anche il default fail-safe su input degeneri/avversariali.

    Serve un `code_token`: una domanda di prezzo senza codice non ha nulla da cercare
    in `righe_listino`, quindi resta NARRATIVE (caveat Fail-Closed)."""
    if route.query_type == "visual":
        return Intent.VISUAL
    if route.code_token and (route.query_type == "code" or _wants_structured(query)):
        return Intent.STRUCTURED
    return Intent.NARRATIVE


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

    # Intento visivo: prevale sulle classi testuali (l'eventuale code_token
    # trovato sopra viene conservato: l'esploso E la riga ricambio).
    visual_hits = [t for t in tokens if t.lower() in VISUAL_MARKERS]
    if visual_hits:
        return QueryRoute("visual", code_token,
                          f"marker visivo '{visual_hits[0]}' -> collection visiva"
                          + (f" + lessicale su {code_token}" if code_token else ""))

    if code_token is None:
        return QueryRoute("natural", None, "nessun token-codice riconosciuto")

    # code vs mixed: quante parole "di contenuto" restano oltre codice e marker?
    marker_set = {m.rstrip(".") for m in MARKERS}
    others = [t for t in tokens
              if t.upper() != code_token and t.lower().rstrip(".") not in marker_set]
    if len(others) <= 1:
        return QueryRoute("code", code_token, reason + " -> sparse-only")
    return QueryRoute("mixed", code_token, reason + f" + {len(others)} parole -> ibrida")
