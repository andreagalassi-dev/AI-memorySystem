"""P4.1 — Ruoli RBAC come tipo chiuso (SPEC §F.3).

Il ruolo viaggia con ogni query e diventa filtro obbligatorio su Qdrant
(P4.5): niente None, niente stringhe libere. StrEnum: il valore e' la
stringa stessa, serializzabile diretta in SQL e nel payload.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    COMMERCIALE = "commerciale"
    TECNICO = "tecnico"
    AMMINISTRAZIONE = "amministrazione"


def parse_role(value: str) -> Role:
    """Valida un ruolo in ingresso. Fail-closed: solleva, mai default."""
    if not isinstance(value, str):
        raise TypeError(f"ruolo deve essere str, non {type(value).__name__}")
    normalized = value.strip().lower()
    try:
        return Role(normalized)
    except ValueError:
        valid = ", ".join(r.value for r in Role)
        raise ValueError(f"ruolo ignoto {value!r}: ammessi {valid}") from None
