"""P3.1 — Guardrail deterministico sui codici (NON-LLM).

Verifica che ogni codice citato in una risposta candidata esista LETTERALMENTE
nell'evidenza recuperata (SPEC §D). Riusa la normalizzazione GT-testata
dell'ingestion (`src/ingestion/codes.py`, `src/ingestion/brands.py`) — nessuna
regex inventata, nessun LLM. Design: docs/superpowers/specs/2026-07-03-p31-
guardrail-codici-design.md.

Tre stadi + aggregazione fail-closed:
  A) extract_candidates  — cattura i token code-shaped, scarta misure/date;
  C) verify_code         — scala a 3 gradini (letterale / variante-separatore /
                           assente) + lookup globale sul catalogo;
     check_answer        — orchestratore: verdetto peggiore-vince.

Regola d'oro: giusto oppure "non lo so", mai sbagliato in silenzio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence

from src.ingestion.brands import all_extract_patterns
from src.ingestion.codes import base_cleanup, canonical

# DEFAULT_CODEY: token candidabile a codice (cifre+lettere len>=4, o >=5 cifre).
# Stesso criterio conservativo del profilo default (src/ingestion/brands.py).
_CODEY = re.compile(r"^(?=.{4,})(?:(?=.*\d)(?=.*[A-Z])[0-9A-Z][0-9A-Z./-]*|\d{5,})$")
# Token generico: allarga il router (ammette . / * - interni).
_TOKEN = re.compile(r"[0-9A-Za-z][0-9A-Za-z./*-]*")

# Unita' di misura standard (ordine: piu' lunghe prima, VAC>V, kW>W, mm>m, Nm>m).
_UNIT = re.compile(
    r"^\d+(?:[.,]\d+)?(?:-\d+(?:[.,]\d+)?)?\s?"
    r"(?:VAC|VDC|kHz|MHz|Hz|kW|mW|mA|Nm|kPa|bar|Pa|ohm|rpm|mm|cm|km|um|ml|kg|mg|dB"
    r"|V|W|A|m|g|L)$",
    re.IGNORECASE,
)
# Forma dimensionale/filetto: cifra x cifra (M6x16, 12x16).
_DIM = re.compile(r"\d\s?[xX×]\s?\d")
# Forma data: 12/05/2024, 12-05-24, ISO 2024-05-12.
_DATE = re.compile(r"^\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}$|^\d{4}-\d{2}-\d{2}$")
# Numero decimale "nudo" (prezzo/quantita' senza unita'): 273.52, 141,98,
# 3.475,78, 3,475.78. Coda di 1-2 cifre decimali dopo l'ultimo separatore.
# NB: e' PROPRIO la forma che canonical() collassa in \d{5,}, facendo scambiare
# un prezzo per un codice (bug eval ampio B: prezzi >=100 -> falso BLOCK). Un
# codice puro-cifre o a gruppi ha coda \d{3,} e non matcha (nessun falso taglio).
_DECIMAL = re.compile(
    r"^\d{1,3}(?:[.,]\d{3})+[.,]\d{1,2}$"  # con separatore delle migliaia
    r"|^\d+[.,]\d{1,2}$"                    # forma piana: 273.52 / 141,98
)

Verdict = Literal["EXISTS", "AMBIGUOUS", "MISS_RETRIEVAL", "ABSENT"]
Gate = Literal["PASS", "VERIFY", "BLOCK"]


@dataclass
class CodeVerdict:
    raw: str
    status: Verdict
    matched_evidence_code: str | None
    reason: str


@dataclass
class GuardrailReport:
    gate: Gate
    verdicts: list[CodeVerdict] = field(default_factory=list)
    reason: str = ""


# --------------------------------------------------------------------- helpers

def _is_code_char(c: str) -> bool:
    """Char che estende un token-codice contiguo (alfanumerico o . - /)."""
    return bool(re.match(r"[0-9A-Za-z./-]", c))


def _literal_key(s: str) -> str:
    """Forma letterale: cleanup markup + rimozione spazi + upper. PRESERVA . - /
    (separatori potenzialmente significativi)."""
    return re.sub(r"\s+", "", base_cleanup(s)).upper()


def _is_codey(token: str) -> bool:
    """Codey direttamente, oppure una volta collassati i separatori
    (es. '340-006-734' -> '340006734')."""
    return bool(_CODEY.match(token.upper()) or _CODEY.match(canonical(token)))


def is_measurement(token: str) -> bool:
    """True per misure/date/prezzi ad alta confidenza (unita', dimensioni, date,
    numeri decimali nudi come i prezzi)."""
    t = token.strip()
    return bool(_UNIT.match(t) or _DIM.search(t) or _DATE.match(t) or _DECIMAL.match(t))


def _family_spans(text: str) -> list[tuple[int, int, str]]:
    """Span (start, end, testo) delle famiglie-codice note, tolleranti a
    spazi/markup interni. Immuni al filtro misura (una famiglia vince sempre).

    Filtro di contenimento: uno span strettamente contenuto in un altro viene
    scartato (es. il '\\d{9}' DeliTools che ricade dentro un codice V0 piu'
    lungo). Si tiene lo span massimale."""
    raw: list[tuple[int, int, str]] = []
    for pat in all_extract_patterns():
        for m in pat.finditer(text):
            raw.append((m.start(), m.end(), m.group()))

    kept: list[tuple[int, int, str]] = []
    for s, e, t in raw:
        if any((os_ <= s and e <= oe) and (os_, oe) != (s, e)
               for os_, oe, _ in raw):
            continue  # strettamente contenuto in un altro span -> scartato
        kept.append((s, e, t))

    # Anti-troncamento: uno span valido non deve tagliare un token-codice
    # contiguo piu' lungo (es. '0USBX4' dentro l'allucinato '0USBX44'). Confine
    # pulito = bordo testo / char non-codice, OPPURE un altro span adiacente
    # (caso codice-doppio fuso 'V0..V0..').
    starts = {s for s, _e, _t in kept}
    ends = {e for _s, e, _t in kept}
    spans: list[tuple[int, int, str]] = []
    for s, e, t in kept:
        left_ok = s == 0 or not _is_code_char(text[s - 1]) or s in ends
        right_ok = e == len(text) or not _is_code_char(text[e]) or e in starts
        if left_ok and right_ok:
            spans.append((s, e, t))
    return spans


# --------------------------------------------------------------- Stadio A

def extract_candidates(text: str) -> list[str]:
    """Token code-shaped nell'ordine d'apparizione, deduplicati per forma
    letterale. Le misure/date non-famiglia vengono scartate."""
    picked: list[tuple[int, str]] = []
    fam_spans = _family_spans(text)
    for start, _end, raw in fam_spans:
        picked.append((start, raw))
    covered = [(s, e) for s, e, _ in fam_spans]

    for m in _TOKEN.finditer(text):
        if any(s <= m.start() < e for s, e in covered):
            continue  # gia' coperto da una famiglia
        tok = m.group()
        if _is_codey(tok) and not is_measurement(tok):
            picked.append((m.start(), tok))

    picked.sort(key=lambda p: p[0])
    out: list[str] = []
    seen: set[str] = set()
    for _pos, tok in picked:
        k = _literal_key(tok)
        if k and k not in seen:
            seen.add(k)
            out.append(tok)
    return out


# --------------------------------------------------------------- Stadio C

def verify_code(
    candidate: str,
    evidence_codes: Sequence[str],
    catalog_lookup: Callable[[str], bool],
) -> CodeVerdict:
    """Scala a 3 gradini contro l'evidenza + lookup globale sul catalogo."""
    lk = _literal_key(candidate)
    ev_literal = {_literal_key(e): e for e in evidence_codes if e}
    if lk in ev_literal:
        return CodeVerdict(candidate, "EXISTS", ev_literal[lk],
                           "match letterale nell'evidenza recuperata")

    can = canonical(candidate)
    ev_canon = {canonical(e): e for e in evidence_codes if e}
    if can and can in ev_canon:
        return CodeVerdict(candidate, "AMBIGUOUS", ev_canon[can],
                           "variante-separatore di un codice presente: "
                           "il separatore puo' essere significativo -> verifica manuale")

    # Non nell'evidenza: distingui miss del retrieval da codice inesistente.
    try:
        in_catalog = bool(catalog_lookup(lk))
    except Exception as exc:  # fail-closed: mai PASS/ABSENT silenzioso su errore
        return CodeVerdict(candidate, "MISS_RETRIEVAL", None,
                           f"lookup catalogo fallito ({exc}) -> verifica manuale")
    if in_catalog:
        return CodeVerdict(candidate, "MISS_RETRIEVAL", None,
                           "esiste a catalogo ma non nell'evidenza recuperata: "
                           "possibile miss del retrieval -> verifica manuale")
    return CodeVerdict(candidate, "ABSENT", None,
                       "codice non a catalogo / allucinato -> bloccato")


# ------------------------------------------------------------- orchestratore

def _degraded(evidence: Sequence[dict]) -> bool:
    for e in evidence:
        if e.get("status") == "degraded":
            return True
        if "ocr_source" in (e.get("warnings") or []):
            return True
    return False


def check_answer(
    answer_text: str,
    evidence: Sequence[dict],
    catalog_lookup: Callable[[str], bool] | None = None,
) -> GuardrailReport:
    """Gate complessivo della risposta (peggiore-vince, fail-closed)."""
    lookup = catalog_lookup or (lambda _k: False)
    evidence_codes = [e.get("code") for e in evidence if e.get("code")]
    verdicts = [verify_code(c, evidence_codes, lookup)
                for c in extract_candidates(answer_text)]

    if any(v.status == "ABSENT" for v in verdicts):
        return GuardrailReport("BLOCK", verdicts,
                               "almeno un codice non a catalogo -> risposta bloccata")
    soft = [v for v in verdicts if v.status in ("AMBIGUOUS", "MISS_RETRIEVAL")]
    if soft or _degraded(evidence):
        why = "codici ambigui/non recuperati" if soft else "evidenza inaffidabile (OCR/degraded)"
        return GuardrailReport("VERIFY", verdicts, f"{why} -> verifica manuale")
    return GuardrailReport("PASS", verdicts,
                           "tutti i codici citati esistono nell'evidenza" if verdicts
                           else "nessun codice citato")
