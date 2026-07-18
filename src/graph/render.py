"""Fase 1.3 Passo 4 — Render deterministico + anti-allucinazione dei prezzi.

Il ramo STRUCTURED legge dati GIA' verificati dal DB (Semantic Layer 1.2) e li fa
formulare in linguaggio naturale da un LLM. L'LLM e' un PURO formattatore: non vede
mai SQL, non genera SQL, non deve inventare valori. Questo modulo contiene le parti
PURE (prompt + verifica), testabili senza LLM.

Due difese contro l'allucinazione, entrambe deterministiche:
  1) i CODICI li verifica il guardrail P3.1 riusato dal grafo (Defense in Depth);
  2) i PREZZI li verifica `verify_price_grounding` qui: estrae i numeri-prezzo dalla
     risposta ignorando conteggi ("1 risultato"), numeri di pagina e cifre interne ai
     codici, li normalizza (virgola/punto/valuta) e pretende che ogni prezzo enunciato
     esista nel DB (nessun prezzo inventato) e che almeno un valore del DB sia
     effettivamente enunciato (grounding positivo). Se fallisce -> REFUSAL.

Design: docs/superpowers/specs/2026-07-09-fase1.3-cervello-unificatore-design.md.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from xml.sax.saxutils import escape, quoteattr

from src.graph.tools.sql_tool import LookupRow
from src.retrieval.synthesis import REFUSAL, strip_citations

SYSTEM_RENDER = f"""\
Sei un formattatore di risposte per un catalogo ricambi B2B. Ricevi dati GIA' \
verificati dal database tra <dati>. Il tuo unico compito e' formularli in \
linguaggio naturale, nella lingua della <domanda>.

REGOLE INVIOLABILI:
1. Usa ESCLUSIVAMENTE i valori dentro <dati>. NON inventare ne' modificare prezzi, \
codici o descrizioni; NON aggiungere dati non presenti.
2. Riporta il prezzo ESATTAMENTE come nel dato, seguito da " EUR" o " €" \
(es. "12,50 €"). Non arrotondare.
3. Cita la fonte tra parentesi col valore esatto: (nome_doc, pag. N).
4. Se <dati> e' vuoto, scrivi ESATTAMENTE: "{REFUSAL}" e fermati.
5. NON generare mai query, SQL o codice. Conciso e fattuale, nessun riempitivo."""


def build_render_prompt(query: str, code_token: str, campo: str,
                        rows: Iterable[LookupRow]) -> str:
    """Prompt utente: dati verificati in XML (grounding confinato) + domanda.

    Serializza SOLO i valori letti dal DB; nessuna traccia di SQL. `code_token` e
    `campo` sono attributi noti (non provengono dal modello)."""
    righe = []
    for r in rows:
        attrs = (f"codice={quoteattr(str(code_token))} "
                 f"campo={quoteattr(str(campo))} "
                 f"valore={quoteattr('' if r.valore is None else str(r.valore))} "
                 f"doc={quoteattr(str(r.doc_stem))} "
                 f"pag={quoteattr(str(r.page))}")
        righe.append(f"  <riga {attrs}/>")
    dati = f"<dati>\n{chr(10).join(righe)}\n</dati>" if righe else "<dati></dati>"
    return f"{dati}\n<domanda>{escape(query)}</domanda>"


# ------------------------------------------------------- value-grounding prezzi

# Simboli/parole di valuta (adiacenza -> il numero e' un prezzo anche se intero).
_CURRENCY = re.compile(r"€|\$|£|\beur\b|\beuro\b|\busd\b|\bchf\b|\bdollar", re.IGNORECASE)
# Numero STANDALONE: non incollato a lettere o cifre (esclude le cifre interne ai
# codici come V09199300001). Ammette separatori . e , interni.
_NUMBER = re.compile(r"(?<![0-9A-Za-z.,])\d[\d.,]*\d|(?<![0-9A-Za-z.,])\d(?![0-9A-Za-z.,])")


def _parse_number(tok: str) -> tuple[Decimal | None, bool]:
    """Normalizza un token numerico -> (Decimal, had_fraction).

    Gestisce sia il formato europeo (1.234,56) sia quello US/DB (1,234.56 / 12.50):
    con entrambi i separatori il PIU' A DESTRA e' il decimale; con uno solo, e'
    trattato come separatore decimale (fail-closed conservativo sull'ambiguita'
    migliaia-vs-decimali). `had_fraction` = il numero e' formattato come decimale."""
    t = tok.strip()
    try:
        if "," in t and "." in t:
            dec = "," if t.rfind(",") > t.rfind(".") else "."
            thou = "." if dec == "," else ","
            intpart, frac = t.rsplit(dec, 1)
            return Decimal(f"{intpart.replace(thou, '')}.{frac}"), True
        if "," in t:
            intpart, frac = t.rsplit(",", 1)
            return Decimal(f"{intpart}.{frac}"), True
        if "." in t:
            intpart, frac = t.rsplit(".", 1)
            return Decimal(f"{intpart}.{frac}"), True
        return Decimal(t), False
    except InvalidOperation:
        return None, False


def _price_candidates(text: str) -> list[Decimal]:
    """Numeri-PREZZO enunciati nel testo: decimali, oppure interi adiacenti a valuta.

    Ignora gli interi 'nudi' (conteggi, numeri di pagina) e le cifre dei codici."""
    out: list[Decimal] = []
    for m in _NUMBER.finditer(text):
        value, had_fraction = _parse_number(m.group())
        if value is None:
            continue
        window = text[max(0, m.start() - 4):m.start()] + text[m.end():m.end() + 6]
        if had_fraction or _CURRENCY.search(window):
            out.append(value)
    return out


def verify_price_grounding(answer: str, db_values: Iterable) -> bool:
    """True se i prezzi enunciati nella risposta sono ANCORATI ai valori del DB.

    Fail-closed: nessun valore numerico a DB, oppure nessun prezzo enunciato, oppure
    un prezzo enunciato che non compare tra i valori del DB -> False (-> REFUSAL)."""
    db = {Decimal(v) for v in db_values if v is not None}
    if not db:
        return False
    stated = set(_price_candidates(strip_citations(answer)))
    if not stated:
        return False
    return stated <= db
