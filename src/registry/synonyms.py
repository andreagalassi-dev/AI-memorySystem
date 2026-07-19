"""Debito §2 — Accesso al dizionario sinonimi su PostgreSQL (Query Expansion).

Stesso pattern del debito §1 (ingestion_profiles.py): connessione INIETTATA,
scritture come unita' transazionali, seed JSON come forma canonica confrontabile.
A runtime l'espansione usa SOLO i termini `approved` di concetti `is_active`:
le future proposte LLM offline restano invisibili finche' non moderate.
"""

from __future__ import annotations

import json
from pathlib import Path

import psycopg

SCHEMA_PATH = Path(__file__).with_name("synonyms_schema.sql")

LANGS = {"IT", "EN", "DE", "ES", "FR", "*"}


def init_schema(conn: psycopg.Connection) -> None:
    """Applica il DDL (idempotente: IF NOT EXISTS ovunque)."""
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def canonical_concept(concept: dict) -> dict:
    """Forma canonica di un concetto: {'note': ..., 'terms': [[term, lang], ...]}.

    I termini sono lowercased e deduplicati preservando l'ordine (l'ordine
    del seed e' l'ordine deterministico d'espansione).
    """
    seen: dict[str, str] = {}
    for term, lang in concept.get("terms", []):
        t = term.strip().lower()
        if not t:
            raise ValueError(f"termine vuoto nel concetto {concept!r}")
        if lang not in LANGS:
            raise ValueError(f"lingua {lang!r} non ammessa (termine {t!r}); "
                             f"ammesse: {sorted(LANGS)}")
        seen.setdefault(t, lang)
    return {"note": concept.get("note"),
            "terms": [[t, lang] for t, lang in seen.items()]}


def load_seed_config(path: Path) -> dict:
    """Legge il seed JSON in forma canonica e validata: {slug: concept}."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    out = {}
    for slug, c in raw["concepts"].items():
        # nel seed i termini sono raggruppati per lingua: {"IT": [...], ...}
        terms = [[t, lang] for lang, ts in c["terms"].items() for t in ts]
        out[slug] = canonical_concept({"note": c.get("note"), "terms": terms})
        if not out[slug]["terms"]:
            raise ValueError(f"concetto {slug!r} senza termini")
    return out


def load_synonyms(conn: psycopg.Connection, only_approved: bool = True) -> dict:
    """Carica i concetti attivi in forma canonica {slug: {note, terms}}."""
    concepts: dict[str, dict] = {}
    ids: dict[int, str] = {}
    for cid, slug, note in conn.execute(
        "SELECT id, slug, note FROM synonym_concepts "
        "WHERE is_active ORDER BY slug"
    ):
        ids[cid] = slug
        concepts[slug] = {"note": note, "terms": []}
    approved_sql = "AND approved" if only_approved else ""
    for cid, term, lang in conn.execute(
        "SELECT concept_id, term, lang FROM synonym_terms "
        f"WHERE true {approved_sql} ORDER BY position, id"
    ):
        if cid in ids:
            concepts[ids[cid]]["terms"].append([term, lang])
    return concepts


def upsert_concept(conn: psycopg.Connection, slug: str, concept: dict) -> int:
    """Crea/aggiorna un concetto e i suoi termini `manual` (transazionale).

    I termini manual non piu' presenti vengono rimossi; le proposte
    `llm_proposed` NON vengono toccate (sopravvivono al re-seed).
    """
    c = canonical_concept(concept)
    row = conn.execute(
        "INSERT INTO synonym_concepts (slug, note) VALUES (%s, %s) "
        "ON CONFLICT (slug) DO UPDATE SET note = EXCLUDED.note, "
        "is_active = true, updated_at = now() RETURNING id",
        (slug, c["note"]),
    ).fetchone()
    cid = row[0]
    for pos, (term, lang) in enumerate(c["terms"]):
        conn.execute(
            "INSERT INTO synonym_terms (concept_id, term, lang, source, "
            "approved, position) VALUES (%s, %s, %s, 'manual', true, %s) "
            "ON CONFLICT (concept_id, term) DO UPDATE SET "
            "lang = EXCLUDED.lang, position = EXCLUDED.position, "
            "source = 'manual', approved = true",
            (cid, term, lang, pos),
        )
    conn.execute(
        "DELETE FROM synonym_terms WHERE concept_id = %s "
        "AND source = 'manual' AND term != ALL(%s)",
        (cid, [t for t, _ in c["terms"]]),
    )
    conn.commit()
    return cid
