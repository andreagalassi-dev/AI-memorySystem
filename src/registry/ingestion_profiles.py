"""Debito tecnico §1 — Accesso ai Profili di Ingestion su PostgreSQL (SSOT).

Postgres e' la fonte di verita' dei profili di normalizzazione codici;
config/brands.json resta solo come seed (scripts/seed_ingestion_profiles.py).
Connessione INIETTATA come nel registro P4.1: le scritture sono unita'
transazionali (commit interno), le letture non toccano la transazione.

Le regex sono TEXT a DB (Postgres non puo' validare la sintassi regex Python):
la validazione re.compile avviene fail-fast qui, prima di ogni scrittura, e di
nuovo al load runtime in brands._config(). Una regex rotta a DB blocca
l'ingestion con errore chiaro — mai degrado silenzioso (principio SPEC).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import psycopg

SCHEMA_PATH = Path(__file__).with_name("ingestion_schema.sql")

_PROFILE_REGEX_FIELDS = ("short_pattern", "unsafe_numeric")
_FAMILY_REGEX_FIELDS = ("extract_pattern", "token_pattern")

_PROFILE_COLS = ("short_pattern", "reason_short", "unsafe_numeric",
                 "reason_unsafe", "passthrough", "fuzzy_repair")
_FAMILY_COLS = ("extract_pattern", "token_pattern", "rejoin",
                "reason_rejoin", "reason_double", "restore_prefix")


def init_schema(conn: psycopg.Connection) -> None:
    """Applica il DDL (idempotente: IF NOT EXISTS ovunque)."""
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def validate_profile(name: str, profile: dict) -> None:
    """Fail-fast: ogni regex del profilo deve compilare. Solleva ValueError."""
    for field in _PROFILE_REGEX_FIELDS:
        _check_regex(profile.get(field), f"profilo {name!r}, campo {field}")
    for fam in profile.get("long_families", []):
        for field in _FAMILY_REGEX_FIELDS:
            _check_regex(fam.get(field),
                         f"profilo {name!r}, famiglia {fam.get('name')!r}, campo {field}")


def _check_regex(pattern: str | None, where: str) -> None:
    if pattern is None:
        return
    try:
        re.compile(pattern)
    except re.error as e:
        raise ValueError(f"regex invalida in {where}: {pattern!r} ({e})") from e


def canonical_profile(profile: dict) -> dict:
    """Forma canonica di un profilo (stessa shape restituita da load_profiles).

    Normalizza le chiavi opzionali del seed JSON (passthrough/rejoin assenti ->
    False) cosi' seed, DB e test confrontano dict identici.
    """
    return {
        "long_families": [
            {
                "name": fam["name"],
                "extract_pattern": fam["extract_pattern"],
                "token_pattern": fam["token_pattern"],
                "rejoin": bool(fam.get("rejoin", False)),
                "reason_rejoin": fam.get("reason_rejoin"),
                "reason_double": fam.get("reason_double"),
                "restore_prefix": fam.get("restore_prefix"),
            }
            for fam in profile.get("long_families", [])
        ],
        "short_pattern": profile.get("short_pattern"),
        "reason_short": profile.get("reason_short"),
        "unsafe_numeric": profile.get("unsafe_numeric"),
        "reason_unsafe": profile.get("reason_unsafe"),
        "passthrough": bool(profile.get("passthrough", False)),
        "fuzzy_repair": bool(profile.get("fuzzy_repair", False)),
    }


def load_seed_config(path: Path) -> dict:
    """Legge il seed JSON (config/brands.json) in forma canonica e VALIDATA."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    profiles = {name: canonical_profile(p) for name, p in raw["profiles"].items()}
    for name, p in profiles.items():
        validate_profile(name, p)
    return {"profiles": profiles, "doc_map": dict(raw["doc_map"])}


def load_profiles(conn: psycopg.Connection) -> dict:
    """Carica i profili attivi in forma canonica: {"profiles": ..., "doc_map": ...}.

    Stessa struttura del vecchio config/brands.json: brands._config() compila le
    regex sopra questo dict senza accorgersi del cambio di fonte.
    """
    profiles: dict[str, dict] = {}
    ids: dict[int, str] = {}
    for (pid, name, short, r_short, unsafe, r_unsafe, passthrough,
         fuzzy) in conn.execute(
        "SELECT id, name, short_pattern, reason_short, unsafe_numeric, "
        "reason_unsafe, passthrough, fuzzy_repair FROM ingestion_profiles "
        "WHERE is_active ORDER BY name"
    ):
        ids[pid] = name
        profiles[name] = {
            "long_families": [],
            "short_pattern": short, "reason_short": r_short,
            "unsafe_numeric": unsafe, "reason_unsafe": r_unsafe,
            "passthrough": passthrough, "fuzzy_repair": fuzzy,
        }
    for (pid, fname, extract, token, rejoin, r_rejoin, r_double,
         r_prefix) in conn.execute(
        "SELECT profile_id, name, extract_pattern, token_pattern, rejoin, "
        "reason_rejoin, reason_double, restore_prefix "
        "FROM ingestion_code_families ORDER BY position, id"
    ):
        if pid in ids:  # famiglie di profili disattivati: ignorate
            profiles[ids[pid]]["long_families"].append({
                "name": fname, "extract_pattern": extract, "token_pattern": token,
                "rejoin": rejoin, "reason_rejoin": r_rejoin,
                "reason_double": r_double, "restore_prefix": r_prefix,
            })
    doc_map = {glob: prof for glob, prof in conn.execute(
        "SELECT d.glob_pattern, p.name FROM ingestion_doc_map d "
        "JOIN ingestion_profiles p ON p.id = d.profile_id "
        "WHERE p.is_active ORDER BY d.priority, d.id"
    )}
    return {"profiles": profiles, "doc_map": doc_map}


def upsert_profile(conn: psycopg.Connection, name: str, profile: dict) -> int:
    """Crea/aggiorna un profilo e le sue famiglie (unita' transazionale).

    Valida le regex PRIMA di scrivere. Le famiglie non piu' presenti nel
    profilo vengono rimosse. Ritorna l'id del profilo.
    """
    p = canonical_profile(profile)
    validate_profile(name, p)
    row = conn.execute(
        "INSERT INTO ingestion_profiles (name, short_pattern, reason_short, "
        "unsafe_numeric, reason_unsafe, passthrough, fuzzy_repair) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
        "ON CONFLICT (name) DO UPDATE SET "
        "short_pattern = EXCLUDED.short_pattern, "
        "reason_short = EXCLUDED.reason_short, "
        "unsafe_numeric = EXCLUDED.unsafe_numeric, "
        "reason_unsafe = EXCLUDED.reason_unsafe, "
        "passthrough = EXCLUDED.passthrough, "
        "fuzzy_repair = EXCLUDED.fuzzy_repair, "
        "is_active = true, updated_at = now() "
        "RETURNING id",
        (name, *(p[c] for c in _PROFILE_COLS)),
    ).fetchone()
    pid = row[0]
    fam_names = [fam["name"] for fam in p["long_families"]]
    for pos, fam in enumerate(p["long_families"]):
        conn.execute(
            "INSERT INTO ingestion_code_families (profile_id, name, "
            "extract_pattern, token_pattern, rejoin, reason_rejoin, "
            "reason_double, restore_prefix, position) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (profile_id, name) DO UPDATE SET "
            "extract_pattern = EXCLUDED.extract_pattern, "
            "token_pattern = EXCLUDED.token_pattern, "
            "rejoin = EXCLUDED.rejoin, "
            "reason_rejoin = EXCLUDED.reason_rejoin, "
            "reason_double = EXCLUDED.reason_double, "
            "restore_prefix = EXCLUDED.restore_prefix, "
            "position = EXCLUDED.position",
            (pid, fam["name"], *(fam[c] for c in _FAMILY_COLS), pos),
        )
    conn.execute(
        "DELETE FROM ingestion_code_families "
        "WHERE profile_id = %s AND name != ALL(%s)",
        (pid, fam_names),
    )
    conn.commit()
    return pid


def set_doc_map(conn: psycopg.Connection, mapping: dict[str, str]) -> None:
    """Allinea la mappa glob -> profilo (unita' transazionale).

    priority = posizione nel dict (l'ordine del seed e' l'ordine di risoluzione).
    I glob non piu' presenti vengono rimossi. I profili devono gia' esistere.
    """
    for prio, (glob, prof) in enumerate(mapping.items()):
        row = conn.execute(
            "SELECT id FROM ingestion_profiles WHERE name = %s", (prof,)
        ).fetchone()
        if row is None:
            conn.rollback()
            raise ValueError(f"doc_map {glob!r} punta al profilo inesistente {prof!r}")
        conn.execute(
            "INSERT INTO ingestion_doc_map (glob_pattern, profile_id, priority) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (glob_pattern) DO UPDATE SET "
            "profile_id = EXCLUDED.profile_id, priority = EXCLUDED.priority",
            (glob, row[0], prio),
        )
    conn.execute(
        "DELETE FROM ingestion_doc_map WHERE glob_pattern != ALL(%s)",
        (list(mapping),),
    )
    conn.commit()
