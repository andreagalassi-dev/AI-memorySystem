"""P4.1 — Ruoli default per categoria (seed del popolamento iniziale).

Decisione utente 2026-07-04: tecnico vede tutto; commerciale e
amministrazione solo price_lists + corporate_brochures. La mappatura vive
in config/default_roles.json (modificabile senza toccare il codice);
`_fallback` copre le categorie impreviste ed e' obbligatoria.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from src.common.roles import Role, parse_role

DEFAULT_ROLES_PATH = Path(__file__).resolve().parents[2] / "config" / "default_roles.json"

FALLBACK_KEY = "_fallback"


def load_default_roles(path: Path = DEFAULT_ROLES_PATH) -> dict[str, list[Role]]:
    """Carica e valida il mapping categoria -> ruoli. Fail-closed su config rotta."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    mapping = {cat: [parse_role(r) for r in roles]
               for cat, roles in raw.items() if not cat.startswith("_doc")}
    if FALLBACK_KEY not in mapping or not mapping[FALLBACK_KEY]:
        raise ValueError(f"config ruoli senza chiave {FALLBACK_KEY!r}: {path}")
    return mapping


def roles_for_category(cat: str | None, mapping: dict[str, list[Role]]) -> list[Role]:
    """Ruoli default per una categoria; fallback per categorie ignote o None."""
    if cat is not None and cat in mapping:
        return mapping[cat]
    return mapping[FALLBACK_KEY]


class DocSeed(NamedTuple):
    category: str | None
    langs: list[str]


def aggregate_docs(payloads: Iterable[dict]) -> dict[str, DocSeed]:
    """Aggrega i payload dello scroll Qdrant in un record per doc stem.

    La prima `cat` non-nulla vince (una sola categoria per doc); le lingue
    si accumulano ordinate e senza duplicati. Payload senza `doc` scartati.
    """
    cats: dict[str, str | None] = {}
    langs: dict[str, set[str]] = {}
    for p in payloads:
        doc = p.get("doc")
        if not doc:
            continue
        if cats.get(doc) is None:
            cats[doc] = p.get("cat")
        langs.setdefault(doc, set())
        if p.get("lang"):
            langs[doc].add(p["lang"])
    return {doc: DocSeed(cats[doc], sorted(langs[doc])) for doc in cats}
