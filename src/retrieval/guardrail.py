"""P3.1 — Guardrail deterministico sui codici (NON-LLM).

Verifica che ogni codice citato in una risposta candidata esista LETTERALMENTE
nell'evidenza recuperata (SPEC §D). Riusa la normalizzazione GT-testata
dell'ingestion (`src/ingestion/codes.py`, `src/ingestion/brands.py`) — nessuna
regex inventata, nessun LLM. Design: docs/superpowers/specs/2026-07-03-p31-
guardrail-codici-design.md.

Tre stadi + aggregazione fail-closed:
  A) extract_candidates  — cattura i token code-shaped, scarta misure/date;
  C) verify_code         — scala a 4 gradini (letterale sul codice / letterale
                           nel TESTO della riga / variante-separatore /
                           assente) + lookup globale sul catalogo;
     check_answer        — orchestratore: verdetto peggiore-vince; il gradino
                           EXISTS_IN_TEXT diventa PASS solo se la pagina della
                           riga d'origine e' fra le citazioni della risposta.

Regola d'oro: giusto oppure "non lo so", mai sbagliato in silenzio.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Literal, Mapping, Sequence

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

Verdict = Literal["EXISTS", "EXISTS_IN_TEXT", "AMBIGUOUS", "MISS_RETRIEVAL", "ABSENT"]
Gate = Literal["PASS", "VERIFY", "BLOCK"]

# Chiave di pagina: (doc_stem, page) normalizzata, per confrontare la riga
# d'origine di un token con le citazioni della risposta.
PageKey = tuple[str, int]


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
    evidence_text_keys: Mapping[str, PageKey] | None = None,
) -> CodeVerdict:
    """Scala a 4 gradini contro l'evidenza + lookup globale sul catalogo.

    `evidence_text_keys` mappa forma-letterale -> (doc, page) dei token
    code-shaped estratti dai TESTI della riga recuperata (descrizione/chunk).
    Default None = comportamento pre-esistente (solo `evidence_codes`)."""
    lk = _literal_key(candidate)
    ev_literal = {_literal_key(e): e for e in evidence_codes if e}
    if lk in ev_literal:
        return CodeVerdict(candidate, "EXISTS", ev_literal[lk],
                           "match letterale nell'evidenza recuperata")

    # SPEC §D: il codice deve esistere letteralmente nella TABELLA recuperata,
    # e la descrizione fa parte della riga. Un token verbatim nella descrizione
    # NON e' pero' un part-number confermato: resta un gradino distinto
    # (EXISTS_IN_TEXT), che l'orchestratore promuove a PASS solo se la pagina
    # d'origine e' fra le citazioni della risposta.
    if evidence_text_keys and lk in evidence_text_keys:
        return CodeVerdict(candidate, "EXISTS_IN_TEXT", None,
                           "presente verbatim nella descrizione della riga "
                           f"recuperata {evidence_text_keys[lk]}")

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


def _page_key(doc, page) -> PageKey | None:
    """Normalizza (doc, page) in una chiave confrontabile; None se incompleta.

    Case-folding sul nome del documento: e' la STESSA semantica della guardia
    citazioni (`synthesis._page_key`), che accetta una citazione con maiuscole
    diverse e la conserva COSI' COM'E' SCRITTA dall'LLM. Senza il case-folding
    anche qui, quella citazione arriva al guardrail come un documento diverso e
    il gradino EXISTS_IN_TEXT non scatta mai (VERIFY invece di PASS) — un
    gradino vivo solo in apparenza. Il confronto resta sul nome INTERO, mai per
    sottostringa: 'rotax_912' non e' 'rotax_912_914_IPC' (lezione ac8da1a)."""
    if doc is None or page is None:
        return None
    try:
        return (str(doc).strip().casefold(), int(page))
    except (TypeError, ValueError):
        return None


def _evidence_text_keys(evidence: Sequence[dict]) -> dict[str, PageKey]:
    """Token code-shaped dei TESTI della riga recuperata (desc + chunk), come
    insieme di forme-letterali -> pagina d'origine.

    L'estrazione e' la STESSA `extract_candidates` usata sulla risposta
    (simmetria): il confronto avviene fra INSIEMI di token, mai con un `in` su
    stringa. Un test di sottostringa farebbe passare 'Max.80m' dentro
    'Max.80mm' — la lezione del fix ac8da1a sulla guardia M1."""
    keys: dict[str, PageKey] = {}
    for e in evidence:
        src_page = _page_key(e.get("doc"), e.get("page"))
        if src_page is None:
            continue
        for src in (e.get("desc"), e.get("text")):
            for tok in extract_candidates(src or ""):
                keys.setdefault(_literal_key(tok), src_page)
    return keys


def check_answer(
    answer_text: str,
    evidence: Sequence[dict],
    catalog_lookup: Callable[[str], bool] | None = None,
    citations: Iterable[tuple[str, int]] | None = None,
) -> GuardrailReport:
    """Gate complessivo della risposta (peggiore-vince, fail-closed).

    `citations` = le fonti (doc, pag) dichiarate dalla risposta. Servono al
    gradino EXISTS_IN_TEXT: un token trovato nella descrizione di una riga
    recuperata e' PASS solo se QUELLA riga e' fra le pagine citate, cosi'
    l'utente ha un percorso di verifica esatto ("il token compare nella
    descrizione della riga a pagina N, e pagina N e' quella mostrata").
    Il caso pericoloso — un codice di rimando pescato da una riga recuperata
    DIVERSA da quella citata e presentato come LA risposta — resta a VERIFY.
    Default None (chiamante che non sa le citazioni) = conservativo: mai PASS
    per questa via."""
    lookup = catalog_lookup or (lambda _k: False)
    evidence_codes = [e.get("code") for e in evidence if e.get("code")]
    text_keys = _evidence_text_keys(evidence)
    verdicts = [verify_code(c, evidence_codes, lookup, text_keys)
                for c in extract_candidates(answer_text)]

    # Una citazione malformata non deve far ESPLODERE il guardrail (sarebbe un
    # fail-open a monte): viene semplicemente ignorata -> resta VERIFY.
    cited = {k for k in (_page_key(c[0], c[1]) for c in (citations or ())
                         if isinstance(c, (tuple, list)) and len(c) == 2) if k}
    unconfirmed = [v for v in verdicts
                   if v.status == "EXISTS_IN_TEXT"
                   and text_keys.get(_literal_key(v.raw)) not in cited]

    if any(v.status == "ABSENT" for v in verdicts):
        return GuardrailReport("BLOCK", verdicts,
                               "almeno un codice non a catalogo -> risposta bloccata")
    soft = [v for v in verdicts if v.status in ("AMBIGUOUS", "MISS_RETRIEVAL")]
    if soft or unconfirmed or _degraded(evidence):
        if soft:
            why = "codici ambigui/non recuperati"
        elif unconfirmed:
            why = ("token presente nella descrizione di una riga NON citata "
                   "dalla risposta")
        else:
            why = "evidenza inaffidabile (OCR/degraded)"
        return GuardrailReport("VERIFY", verdicts, f"{why} -> verifica manuale")
    return GuardrailReport("PASS", verdicts,
                           "tutti i codici citati esistono nell'evidenza" if verdicts
                           else "nessun codice citato")
