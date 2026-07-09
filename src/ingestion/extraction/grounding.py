"""Fase 1.2 — Strato 2 Fail-Closed: grounding verbatim deterministico (NON-LLM).

Lo strato FORMA (`schema.RigaGrezza`) blocca l'invenzione di STRUTTURA; questo
strato blocca l'invenzione di VALORE: un prezzo/descrizione plausibile ma falso
passa Pydantic, ma se il suo CODICE non e' fisicamente presente nel testo grezzo
della pagina, la riga e' fabbricata e va scartata.

Regola operativa: il `codice_normalizzato` di una riga (calcolato da NOI con
`codes.canonical`) deve corrispondere allo skeleton canonico di un token
code-shaped effettivamente presente nel testo della pagina. Niente match ->
`Scarto(CODICE_NON_ANCORATO)`, mai in silenzio nelle tabelle-dati.

Il confronto e' sullo skeleton `canonical` (solo alfanumerici, upper), quindi un
codice presente come `340-006-734` ancora una riga che emette `340006734` (stesso
skeleton), ma NON ancora un `340006999` allucinato.

Perche' NON riuso `guardrail.extract_candidates`: quella estrazione dipende dai
profili-brand su Postgres (`brands.all_extract_patterns`) e solleverebbe a ogni
chiamata senza DB. Il grounding e' hot-path e dev'essere puro/DB-free/offline-
testabile: tokenizza in proprio i run code-shaped (stessa forma di `guardrail._TOKEN`)
e riusa solo la normalizzazione GT-testata `codes.canonical`. Non serve
*classificare* i codici della pagina (il filtro misure/date del guardrail), solo
verificare la *presenza* di un codice gia' noto: questo alza anche il recall del
grounding (cattura i codici brand corti che `extract_candidates` scarterebbe).

Il testo di grounding e' la sorgente su cui si e' basata l'estrazione (text-layer
del PDF born-digital, o l'OCR grezzo del modello): grounding.py e' agnostico alla
sorgente, la sceglie la pipeline.

Design: docs/superpowers/specs/2026-07-09-fase1.2-estrazione-massiva-design.md.
"""

from __future__ import annotations

import logging
import re

from src.ingestion.codes import canonical
from src.ingestion.extraction.schema import MotivoScarto, RigaListino, Scarto

logger = logging.getLogger(__name__)

# Run code-shaped: alfanumerico + separatori interni . / * - (stessa forma di
# guardrail._TOKEN). PRESERVA i separatori nel token cosi' 'V0-123' resta unito e
# `canonical` ne ricava lo skeleton 'V0123'. DB-free per contratto.
_CODE_RUN = re.compile(r"[0-9A-Za-z][0-9A-Za-z./*-]*")


def page_code_set(page_text: str) -> set[str]:
    """Skeleton canonici dei token code-shaped presenti nel testo della pagina.

    Indice di presenza (non un classificatore di codici): gli skeleton vuoti
    (token degeneri) sono esclusi. Deterministico e DB-free.
    """
    return {c for m in _CODE_RUN.finditer(page_text) if (c := canonical(m.group()))}


def is_grounded(codice_normalizzato: str, page_codes: set[str]) -> bool:
    """True se lo skeleton del codice compare tra i codici presenti nella pagina.

    `codice_normalizzato` e' gia' canonico (prodotto da `RigaListino.from_grezza`).
    Uno skeleton vuoto non e' mai ancorato (fail-closed sui codici degeneri).
    """
    return bool(codice_normalizzato) and codice_normalizzato in page_codes


def _to_scarto(riga: RigaListino) -> Scarto:
    """Costruisce il record di scarto per un codice non ancorato (audit completo)."""
    return Scarto(
        doc_stem=riga.doc_stem,
        page=riga.page,
        codice_originale=riga.codice_originale,
        codice_normalizzato_tentato=riga.codice_normalizzato,
        payload_raw={
            "codice_originale": riga.codice_originale,
            "descrizione": riga.descrizione,
            # Decimal -> str per un payload JSON-safe (finira' in jsonb)
            "prezzo": None if riga.prezzo is None else str(riga.prezzo),
        },
        motivo=MotivoScarto.CODICE_NON_ANCORATO,
    )


def partition(
    rows: list[RigaListino], page_text: str
) -> tuple[list[RigaListino], list[Scarto]]:
    """Divide le righe di una pagina in ancorate (valide) e non-ancorate (scarti).

    Args:
      rows: righe gia' validate-di-forma e normalizzate (`RigaListino`).
      page_text: testo grezzo della pagina sorgente su cui verificare la presenza.

    Ritorna: (valide, scarti). Nessuna riga non-ancorata attraversa la barriera.
    """
    page_codes = page_code_set(page_text)
    valide: list[RigaListino] = []
    scarti: list[Scarto] = []
    for riga in rows:
        if is_grounded(riga.codice_normalizzato, page_codes):
            valide.append(riga)
        else:
            logger.warning(
                "grounding: codice non ancorato doc_stem=%s page=%d codice=%r norm=%r",
                riga.doc_stem, riga.page, riga.codice_originale, riga.codice_normalizzato,
            )
            scarti.append(_to_scarto(riga))
    return valide, scarti
