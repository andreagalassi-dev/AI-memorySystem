"""P3.4 — Sintesi ancorata (text-reasoner): parti pure + chiamata LLM.

L'LLM e' un SINTETIZZATORE VINCOLATO: risponde solo dall'evidenza, cita
doc+pagina o rifiuta. NON valida (codici -> P3.1, confidenza -> P3.2). Il
grounding e' confinato in tag XML: la domanda non si confonde con l'evidenza.
Design: docs/superpowers/specs/2026-07-04-p34-llm-spike-design.md.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import NamedTuple

from src.graph.llm import LanguageModel
from xml.sax.saxutils import escape, quoteattr

logger = logging.getLogger(__name__)

REFUSAL = "Non ho evidenza sufficiente per rispondere."

SYSTEM_SYNTHESIS = f"""\
Sei un assistente tecnico per ricambi manifatturieri. Rispondi ESCLUSIVAMENTE \
usando il contenuto tra <evidenza>...</evidenza>. Non usare conoscenza esterna.

REGOLE INVIOLABILI:
1. Non affermare NULLA che non sia scritto nell'<evidenza>. Se nell'<evidenza>
   non c'e' NIENTE di pertinente alla <domanda>, scrivi ESATTAMENTE:
   "{REFUSAL}" e fermati.
2. QUANDO NON rifiutare: se l'<evidenza> identifica il pezzo o l'argomento
   chiesto, DEVI rispondere con cio' che c'e', anche se e' parziale.
   L'assenza del codice, del prezzo o di altri dettagli NON e' un motivo per
   rifiutare: riporta il dato presente, cita la fonte e dichiara cosa manca
   (es. "codice non indicato in questa fonte"). Un'informazione parziale ma
   CITATA e' preferibile a un rifiuto; un'informazione non citata resta vietata.
3. Ogni affermazione cita la fonte nella forma ESATTA (nome_documento, pag. N).
   COPIA i due valori dal tag <documento> che stai usando, senza cambiarli:
   nome_documento = valore dell'attributo doc (il nome reale, MAI la parola
   "doc"); N = valore dell'attributo pag di QUEL <documento>, nient'altro.
   Es.: da <documento doc="rotax_912_914_IPC" pag="22"> scrivi
   (rotax_912_914_IPC, pag. 22).
   VIETATO usare come N un numero preso dal CONTENUTO: il codice ricambio, il
   prezzo, la quantita' e il numero di posizione NON sono numeri di pagina.
   Es. sbagliato: se il codice 998877 compare in <documento doc="catalogo_x"
   pag="12">, la fonte e' (catalogo_x, pag. 12) e MAI (catalogo_x, pag. 998877).
   Anche quando la fonte e' un foglio di calcolo citi il valore dell'attributo
   pag, es. (listino_ricambi.xlsx, pag. 0): dentro la citazione MAI il nome del
   foglio, MAI la parola "foglio", MAI testo al posto di N.
   Nessuna citazione = non affermare.
4. NON inventare codici ricambio: riporta un codice SOLO se compare
   letteralmente in un <documento>. Nel dubbio, ometti il codice e dillo.
5. Rispondi nella lingua della <domanda>, anche se l'evidenza e' in altra lingua.
6. Conciso e fattuale: nessuna supposizione, nessun riempitivo."""

_CITE = re.compile(r"\(([^,()]+),\s*pag\.?\s*(\d+)\)")


def _content(pl: dict) -> str:
    if pl.get("type") == "table_row":
        parts = [str(pl[k]) for k in ("code", "desc") if pl.get(k)]
        line = " — ".join(parts) if parts else (pl.get("text") or "")
        extras = []
        if pl.get("qty") is not None:
            extras.append(f"qty {pl['qty']}")
        if pl.get("price") is not None:
            extras.append(f"prezzo {pl['price']}")
        if extras:
            line += " (" + ", ".join(extras) + ")"
        return line
    return pl.get("text") or pl.get("desc") or ""


def format_evidence_xml(payloads: Sequence[dict]) -> str:
    """Evidenza recuperata in tag XML: <documento doc pag lang>contenuto."""
    rows = []
    for pl in payloads:
        attrs = (f"doc={quoteattr(str(pl.get('doc', '')))} "
                 f"pag={quoteattr(str(pl.get('page', '')))} "
                 f"lang={quoteattr(str(pl.get('lang', '')))}")
        rows.append(f"  <documento {attrs}>\n    "
                    f"{escape(_content(pl)).strip()}\n  </documento>")
    return f"<evidenza>\n{chr(10).join(rows)}\n</evidenza>" if rows \
        else "<evidenza></evidenza>"


def build_prompt(query: str, payloads: Sequence[dict]) -> str:
    """Prompt utente: evidenza XML + domanda XML (grounding confinato)."""
    return (f"{format_evidence_xml(payloads)}\n"
            f"<domanda>{escape(query)}</domanda>")


def _clean_doc(label: str) -> str:
    """Normalizza il nome-doc citato: toglie un'eventuale etichetta
    'doc'/'documento' o la forma-attributo XML `doc="..."` che il modello a
    volte copia, e gli apici circostanti."""
    # Strip SOLO se doc/documento e' un'etichetta separata (seguita da :=/spazio),
    # non quando e' il prefisso del nome reale (es. 'doc_x' resta 'doc_x').
    s = re.sub(r'(?i)^\s*(?:doc(?:umento)?)(?:\s*[:=]\s*|\s+)', "", label).strip()
    return s.strip("\"'")


def extract_citations(text: str) -> list[tuple[str, int]]:
    """Estrae le citazioni (doc, pag. N) dalla risposta dell'LLM."""
    return [(_clean_doc(m.group(1)), int(m.group(2))) for m in _CITE.finditer(text)]


def strip_citations(text: str) -> str:
    """Rimuove gli span di citazione: il guardrail codici deve leggere le
    AFFERMAZIONI, non i nomi-documento (che contengono sotto-token code-shaped,
    es. lycoming_IGO540_PC116 -> falso-positivo). Integrazione P3.1↔P3.4."""
    return _CITE.sub("", text)


# ------------------------------------------- guardia deterministica citazioni

class CitationCheck(NamedTuple):
    """Esito della guardia: testo ripulito, citazioni tenute, citazioni scartate."""

    text: str
    kept: list[tuple[str, int]]
    dropped: list[tuple[str, int]]


def _page_key(doc, page) -> tuple[str, int] | None:
    """Chiave (documento, pagina) confrontabile; None se incompleta/non numerica.

    Stessa semantica di `guardrail._page_key` — uguaglianza sul nome INTERO,
    pagina intera — con in piu' il case-folding: una differenza di maiuscole e'
    una variazione di trascrizione, non un altro documento. MAI un confronto
    per sottostringa: 'rotax_912' non e' 'rotax_912_914_IPC' (lezione ac8da1a)."""
    if doc is None or page is None:
        return None
    try:
        return (str(doc).strip().casefold(), int(page))
    except (TypeError, ValueError):
        return None


def retrieved_pages(payloads: Sequence[Mapping]) -> set[tuple[str, int]]:
    """Insieme delle coppie (documento, pagina) EFFETTIVAMENTE recuperate."""
    keys = {_page_key(p.get("doc"), p.get("page")) for p in payloads}
    return {k for k in keys if k is not None}


def verify_citations(text: str, payloads: Sequence[Mapping]) -> CitationCheck:
    """Guardia deterministica NON-LLM: una citazione vale solo se la coppia
    (documento, pagina) e' fra quelle recuperate e passate al modello.

    CAUSA DEL GUASTO ORIGINALE: NON ACCERTATA, e va detto. Sul momento
    l'avevo attribuita alla formulazione della regola 3 ("N e' SEMPRE un
    numero"), perche' un A/B dava 3/3 sbagliato col prompt nuovo e 3/3 giusto
    col vecchio. Rimisurando piu' tardi, con la regola difettosa ricostruita
    verbatim e la sintesi chiamata in isolamento, TUTTI e tre i prompt
    (difettoso, attuale, storico) citano `pag. 84` 3 volte su 3: il difetto non
    si riproduce. Indizio dell'alternativa piu' probabile: il gate che
    registro' il guasto duro impiego' 58.2 min contro i ~21 abituali, cioe' la
    macchina era in affanno e la generazione era degradata. Morale: la regola 3
    irrigidita resta sensata ma NON e' una difesa provata; l'unica difesa
    provata e' questa guardia, che non dipende da come il modello si comporta.

    Nasce dal caso reale `C_EN_03` ("code 874300"): il modello ha scritto
    `(rotax_912_914_IPC, pag. 874300)` — il codice ricambio al posto del numero
    di pagina — e il sistema ha accettato, restando ALTA, una citazione a una
    pagina che non aveva mai recuperato. Le citazioni non verificate vengono
    tolte SIA dalla lista strutturata SIA dal testo mostrato all'utente: una
    fonte falsa a schermo e' "sbagliato in silenzio" anche se accanto c'e' una
    fonte buona. Il chiamante decide il fail-closed sul caso "zero citazioni".
    """
    valid = retrieved_pages(payloads)
    kept: list[tuple[str, int]] = []
    dropped: list[tuple[str, int]] = []

    def _keep(m: re.Match) -> str:
        cited = (_clean_doc(m.group(1)), int(m.group(2)))
        if _page_key(*cited) in valid:
            kept.append(cited)
            return m.group(0)
        dropped.append(cited)
        return ""

    out = _CITE.sub(_keep, text)
    if dropped:
        out = re.sub(r"[ \t]{2,}", " ", out)
        out = re.sub(r"\s+([.,;:])", r"\1", out).strip()
        logger.warning("guardia citazioni: scartate %d citazioni non recuperate: %s",
                       len(dropped), dropped)
    return CitationCheck(out, kept, dropped)


def synthesize(llm: "LanguageModel", query: str, payloads: Sequence[dict]) -> str:
    """Chiama il ragionatore per la sintesi ancorata (integrazione)."""
    return llm.generate(SYSTEM_SYNTHESIS, build_prompt(query, payloads))
