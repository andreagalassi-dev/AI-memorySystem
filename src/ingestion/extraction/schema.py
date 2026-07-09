"""Fase 1.2 — Contratti Pydantic dell'estrazione massiva (strato FORMA).

Due modelli distinti (design: docs/superpowers/specs/2026-07-09-fase1.2-estrazione-
massiva-design.md):

  - `RigaGrezza`  : cio' che il modello DeepSeek-OCR-2 DEVE produrre (response_model
                    di Instructor). `extra="forbid"` e' la guardia anti-invenzione
                    di STRUTTURA: un campo in piu' inventato dal modello -> errore.
  - `RigaListino` : la riga persistita = grezza + metadati calcolati da NOI. Il
                    `codice_normalizzato` (chiave del merge Fase 2) NON viene dal
                    modello: lo produciamo con `codes.canonical`. Il modello e' un
                    passacarte ignorante, non fonte di verita'.

Nota strict: l'output del modello e' JSON, quindi NON si usa `strict=True` globale
(over-rigetterebbe numeri/stringhe legittimi). La forma la garantiscono
`extra="forbid"` + i vincoli di campo; l'invenzione di VALORE (prezzo plausibile ma
falso) la intercetta il grounding deterministico a valle (`grounding.py`).
"""

from __future__ import annotations

from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.ingestion.codes import canonical


class RigaGrezza(BaseModel):
    """Riga come la propone il modello. Nessun metadato di provenienza qui."""

    model_config = ConfigDict(extra="forbid")

    codice_originale: str = Field(min_length=1)
    descrizione: str | None = None
    prezzo: Decimal | None = Field(default=None, ge=0)

    @field_validator("codice_originale")
    @classmethod
    def _codice_non_vuoto(cls, v: str) -> str:
        """Rifiuta i codici di soli spazi (min_length non li intercetta)."""
        if not v.strip():
            raise ValueError("codice_originale vuoto o di soli spazi")
        return v


class RigaListino(BaseModel):
    """Riga persistita in `estrazione.righe_listino`.

    `codice_normalizzato` e' calcolato da noi (`codes.canonical`), non accettato dal
    modello. Costruire con `from_grezza`, mai a mano, per garantire l'invariante.
    """

    codice_originale: str
    descrizione: str | None
    prezzo: Decimal | None
    codice_normalizzato: str
    doc_stem: str
    page: int

    @classmethod
    def from_grezza(cls, grezza: RigaGrezza, *, doc_stem: str, page: int) -> "RigaListino":
        """Arricchisce una riga grezza con la normalizzazione deterministica + provenienza."""
        return cls(
            codice_originale=grezza.codice_originale,
            descrizione=grezza.descrizione,
            prezzo=grezza.prezzo,
            codice_normalizzato=canonical(grezza.codice_originale),
            doc_stem=doc_stem,
            page=page,
        )


class MotivoScarto(str, Enum):
    """Perche' una riga e' finita in `estrazione.scarti_estrazione` (Fail-Closed)."""

    SHAPE_INVALID = "shape_invalid"          # forma/tipo non validi (Pydantic)
    CODICE_NON_ANCORATO = "codice_non_ancorato"  # codice non presente verbatim (fabbricato)


class Scarto(BaseModel):
    """Record di una riga scartata, destinato a `estrazione.scarti_estrazione`."""

    doc_stem: str
    page: int
    codice_originale: str | None = None
    codice_normalizzato_tentato: str | None = None
    payload_raw: dict | None = None          # cosa ha emesso il modello
    motivo: MotivoScarto
