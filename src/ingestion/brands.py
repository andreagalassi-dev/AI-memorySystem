"""P2.6 — Registro profili brand: normalizzazione codici a CONFIGURAZIONE.

Chiude il debito della gate decision P1 §5: le regole di famiglia-codice non
vivono hardcoded in Python. Dal debito tecnico §1 (post-P5) la fonte di verita'
sono le tabelle Postgres dei Profili di Ingestion (src/registry/
ingestion_profiles.py); config/brands.json resta SOLO come seed
(scripts/seed_ingestion_profiles.py). FAIL-CLOSED: DB irraggiungibile o
profili non seedati -> errore chiaro, nessun fallback su JSON stantio
(principio SPEC: mai sbagliato in silenzio). Il profilo `default` e' il
guardrail anti rabbit-hole: NON normalizza (nessun rejoin, nessuna regex
inventata), fa solo cleanup sicuro + rilevamento conservativo del
token-codice, e marca tutto `default_passthrough`.

Lo stesso registro alimenta il router (famiglie Tier A) e il chunker
(rilevamento colonna-codice): una sola fonte di verita'.
"""

from __future__ import annotations

import fnmatch
import re
from functools import lru_cache

from src.ingestion.codes import base_cleanup, canonical

# Rilevamento CONSERVATIVO del profilo default: token singolo con cifre+lettere
# (len>=4) o solo cifre (len>=5). Niente virgole (prezzi), niente spazi.
DEFAULT_CODEY = re.compile(r"^(?=.{4,})(?:(?=.*\d)(?=.*[A-Z])[0-9A-Z][0-9A-Z./-]*|\d{5,})$")


def _load_from_db() -> dict:
    import psycopg

    from src.backend.clients.postgres_client import get_pg_conn
    from src.registry.ingestion_profiles import load_profiles
    try:
        with get_pg_conn() as conn:
            cfg = load_profiles(conn)
    except psycopg.errors.UndefinedTable as e:
        raise RuntimeError(
            "Tabelle dei Profili di Ingestion assenti: eseguire "
            "`uv run scripts/seed_ingestion_profiles.py`") from e
    except psycopg.OperationalError as e:
        raise RuntimeError(
            "Profili di ingestion non caricabili: Postgres irraggiungibile "
            "(docker compose up -d postgres). Fail-closed: nessun fallback "
            "su config/brands.json") from e
    if "default" not in cfg["profiles"]:
        raise RuntimeError(
            "Profilo 'default' assente a DB: eseguire "
            "`uv run scripts/seed_ingestion_profiles.py`")
    return cfg


def reload_profiles() -> None:
    """Invalida la cache di processo (test, futura UI admin dei profili)."""
    _config.cache_clear()


@lru_cache(maxsize=1)
def _config() -> dict:
    cfg = _load_from_db()
    for name, p in cfg["profiles"].items():
        for fam in p.get("long_families", []):
            fam["_extract"] = re.compile(fam["extract_pattern"])
            fam["_token"] = re.compile(fam["token_pattern"])
        if p.get("short_pattern"):
            p["_short"] = re.compile(p["short_pattern"])
        if p.get("unsafe_numeric"):
            p["_unsafe"] = re.compile(p["unsafe_numeric"])
        p["_name"] = name
    return cfg


def get_profile(name: str) -> dict:
    return _config()["profiles"][name]


def resolve(doc_name: str) -> dict:
    """Profilo per nome documento (glob doc_map); non mappato -> default."""
    for pattern, prof in _config()["doc_map"].items():
        if fnmatch.fnmatch(doc_name, pattern):
            return get_profile(prof)
    return get_profile("default")


def all_token_patterns() -> list[re.Pattern]:
    """Famiglie canoniche di TUTTI i brand (per il router Tier A)."""
    return [fam["_token"] for p in _config()["profiles"].values()
            for fam in p.get("long_families", [])]


def all_extract_patterns() -> list[re.Pattern]:
    """Regex di ESTRAZIONE (tolleranti a spazi/markup, NON ancorate) di TUTTI i
    brand. Usate dal guardrail P3.1 per catturare codici spezzati da spazi
    (V0 91568 00007) e codici-doppi fusi. Vedi all_token_patterns per la forma
    canonica ancorata."""
    return [fam["_extract"] for p in _config()["profiles"].values()
            for fam in p.get("long_families", [])]


def normalize_cell(cell: str, profile: dict) -> tuple[list[str], list[dict]]:
    """Motore generico di normalizzazione cella guidato dal profilo.

    Replica esattamente il comportamento P1.5+ quando profile=mamoli
    (regressione byte-identica sugli output del normalizzatore).
    """
    clean = base_cleanup(cell)
    codes: list[str] = []
    logs: list[dict] = []

    for fam in profile.get("long_families", []):
        found = fam["_extract"].findall(clean)
        if not found:
            continue
        for m in found:
            can = canonical(m)
            had_split = bool(re.search(r"[\s*_`]", m.strip())) or len(found) > 1
            logs.append({
                "original": cell.strip(), "normalized": can,
                "type": "safe_rejoin" if had_split else "safe_exact_cleanup",
                "reason": (fam["reason_double"] if len(found) > 1
                           else fam["reason_rejoin"]),
            })
            codes.append(can)
        return codes, logs

    if profile.get("_short") and profile["_short"].match(clean):
        logs.append({"original": cell.strip(), "normalized": clean,
                     "type": "safe_exact_cleanup",
                     "reason": profile["reason_short"]})
        return [clean], logs

    if profile.get("passthrough") and DEFAULT_CODEY.match(clean.upper()) \
            and clean.upper() == clean:
        # default: il token e' gia' pulito e "codey" -> passa INTATTO, loggato
        logs.append({"original": cell.strip(), "normalized": clean,
                     "type": "default_passthrough",
                     "reason": "profilo default: token-codice rilevato, nessuna "
                               "normalizzazione applicata"})
        return [clean], logs

    if profile.get("_unsafe"):
        only = canonical(clean)
        if profile["_unsafe"].fullmatch(only):
            logs.append({"original": cell.strip(), "normalized": None,
                         "type": "unsafe_candidate",
                         "reason": profile["reason_unsafe"]})
    return [], logs
