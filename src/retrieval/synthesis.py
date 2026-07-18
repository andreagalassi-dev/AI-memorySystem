"""P3.4 — Sintesi ancorata (text-reasoner): parti pure + chiamata LLM.

L'LLM e' un SINTETIZZATORE VINCOLATO: risponde solo dall'evidenza, cita
doc+pagina o rifiuta. NON valida (codici -> P3.1, confidenza -> P3.2). Il
grounding e' confinato in tag XML: la domanda non si confonde con l'evidenza.
Design: docs/superpowers/specs/2026-07-04-p34-llm-spike-design.md.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from src.graph.llm import LanguageModel
from xml.sax.saxutils import escape, quoteattr

REFUSAL = "Non ho evidenza sufficiente per rispondere."

SYSTEM_SYNTHESIS = f"""\
Sei un assistente tecnico per ricambi manifatturieri. Rispondi ESCLUSIVAMENTE \
usando il contenuto tra <evidenza>...</evidenza>. Non usare conoscenza esterna.

REGOLE INVIOLABILI:
1. Se la risposta non e' deducibile dall'<evidenza>, scrivi ESATTAMENTE:
   "{REFUSAL}" e fermati.
2. Ogni affermazione cita la fonte tra parentesi indicando il VALORE ESATTO
   dell'attributo doc e la pagina del <documento> usato, es.:
   (rotax_912_914_IPC, pag. 22). Scrivi il nome reale del documento, MAI la
   parola "doc". Nessuna citazione = non affermare.
3. NON inventare codici ricambio: riporta un codice SOLO se compare
   letteralmente in un <documento>. Nel dubbio, ometti il codice.
4. Rispondi nella lingua della <domanda>, anche se l'evidenza e' in altra lingua.
5. Conciso e fattuale: nessuna supposizione, nessun riempitivo."""

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

def synthesize(llm: "LanguageModel", query: str, payloads: Sequence[dict]) -> str:
    """Chiama il ragionatore per la sintesi ancorata (integrazione)."""
    return llm.generate(SYSTEM_SYNTHESIS, build_prompt(query, payloads))
