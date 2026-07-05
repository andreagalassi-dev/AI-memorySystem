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

# ---- Debito §4: pass di riparazione "ultima ratio" (gate: profile.fuzzy_repair)
# Mappa CHIUSA di glifi confondibili (mai distanza di edit generica: 2->3
# impossibile per costruzione). Max 1 sostituzione per token.
CONFUSABLE = {"O": "0", "0": "O", "I": "1", "1": "I",
              "S": "5", "5": "S", "B": "8", "8": "B"}
# Prezzi in formato europeo ('68,00', '1.234,50'): mascherati prima del
# row-rejoin — sono la causa misurata dei codici spezzati tra celle (P231 r29).
PRICE = re.compile(r"\b\d{1,3}(?:\.\d{3})*,\d{2}\b")
# Run numerico orfano (cifre e spazi, senza alnum adiacente) per prefix-restore.
DIGIT_RUN = re.compile(r"(?<![0-9A-Z])\d[\d ]{8,14}\d(?![0-9A-Z])")


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
            if fam.get("restore_run"):
                fam["_restore_run"] = re.compile(fam["restore_run"])
        if p.get("short_pattern"):
            p["_short"] = re.compile(p["short_pattern"])
        if p.get("unsafe_numeric"):
            p["_unsafe"] = re.compile(p["unsafe_numeric"])
        if p.get("assembly_pattern"):
            p["_assembly"] = re.compile(p["assembly_pattern"])
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


# --------------------------------------------- Debito §4: pass di riparazione
# I codici riparati NON sono mai safe_*: tipo `fuzzy_repaired`, e a valle il
# chunk che li porta va marcato (warning 'fuzzy_code') -> confidence cappata
# a MEDIA. Decisioni utente 2026-07-05: OFF di default, per-profilo a DB;
# mappa confusable chiusa; collision check obbligatorio.

def fuzzy_cell(cell: str, profile: dict,
               families: set[str] | None = None) -> tuple[str | None, dict | None]:
    """Confusable 1-char SOLO sul prefisso letterale di famiglia (O<->0, ...).

    Con alfabeti permissivi ([0-9A-Z]) una sostituzione a meta' token non e'
    mai PROVABILMENTE giusta (il pattern accetta entrambe le forme): l'unica
    regione dove l'errore e' dimostrabile e' il prefisso letterale
    (restore_prefix, es. 'VO'!='V0'). Quindi: max 1 sostituzione confusable,
    solo nel prefisso, e il token riparato deve matchare la forma canonica.
    Il resto del token passa la STESSA validazione dei codici safe nativi
    (un'eventuale corruzione della coda e' indistinguibile senza catalogo,
    per il pass safe come per questo). Token gia' validi mai toccati (V012
    non puo' diventare V013: '2'->'3' non e' nella mappa chiusa). Famiglie
    senza restore_prefix: nessuna riparazione. Collision check al chiamante.

    `families`: se non None, ripara SOLO le famiglie elencate (per nome).
    Gate anti-fabbricazione del chunker corpus: una famiglia va riparata solo
    dove e' dimostrabilmente presente (codici safe della stessa famiglia
    sulla pagina), altrimenti su layout estranei — es. pagine listino Mamoli
    coi codici zero-heavy non-V0 — il restore inventa codici. None = tutte
    (comportamento del normalizzatore sulle tabelle GT, invariato).
    """
    if not profile.get("fuzzy_repair"):
        return None, None
    can = canonical(base_cleanup(cell))
    if not can:
        return None, None
    fams = profile.get("long_families", [])
    if any(f["_token"].fullmatch(can) for f in fams):
        return None, None          # gia' valido: mai alterare
    for fam in fams:
        if families is not None and fam["name"] not in families:
            continue
        pref = fam.get("restore_prefix")
        if not pref or len(can) <= len(pref):
            continue
        head = can[:len(pref)]
        mism = [i for i in range(len(pref)) if head[i] != pref[i]]
        if len(mism) != 1 or CONFUSABLE.get(head[mism[0]]) != pref[mism[0]]:
            continue               # 0 o >1 errori, o glifo non confondibile
        repaired = pref + can[len(pref):]
        if fam["_token"].fullmatch(repaired):
            return repaired, {
                "original": cell.strip(), "normalized": repaired,
                "type": "fuzzy_repaired",
                "reason": f"confusable 1-char sul prefisso {pref!r} "
                          f"({fam['name']}): glifo provabilmente errato "
                          "corretto (cap MEDIA)"}
    return None, None


def repair_row(cells: list[str], profile: dict, known: set[str],
               families: set[str] | None = None) -> tuple[list[str], list[dict]]:
    """Riparazione a livello di RIGA sulle tabelle fuse (debito §4).

    Docling puo' spezzare un codice tra celle adiacenti col prezzo in mezzo
    ('V0 92750 68,00' | '00001') o perdere il prefisso di famiglia
    ('94094 00000'). Coi prezzi MASCHERATI:
      A) row-rejoin: regex di famiglia su FINESTRE DI 2 CELLE ADIACENTI —
         non sull'intera riga: sulle righe multi-record (4 record/riga nelle
         pagine listino) la riga piatta salda frammenti di record DIVERSI
         fabbricando codici; i casi di split misurati (P231 r30) stanno in
         una coppia adiacente.
      B) prefix-restore: prefisso di famiglia (config a DB) su run numerici
         orfani DENTRO UNA SINGOLA CELLA, con tre vincoli anti-fabbricazione:
           1. il run deve avere la forma STAMPATA della famiglia
              (restore_run a DB, es. '^\\d{5} \\d{5}$' Mamoli): senza,
              si saldano numero-modello + frammento + qty ('6500 00001 1');
           2. mai run coi gruppi tutti identici ('00001 00001'): e' l'eco
              delle colonne-quantita' di record affiancati, non un codice;
           3. i match delle regex di famiglia sono mascherati prima della
              ricerca: un run sotto-finestra di un codice gia' estraibile
              ('V00 3215 00001' -> '3215 00001') non e' orfano.
         Tutti i casi misurati (P231 r45/r50, corpus p228/p230) stanno in
         una cella singola.
    `known` = codici gia' estratti in modo safe nella pagina: un candidato che
    collide NON viene emesso (era gia' visibile) e nessuna riparazione puo'
    coincidere con un codice esistente per errore.
    `families` = gate anti-fabbricazione per-famiglia (vedi fuzzy_cell).
    """
    if not profile.get("fuzzy_repair"):
        return [], []
    clean = [PRICE.sub(" ", base_cleanup(c)) for c in cells]
    windows = [" ".join(clean[i:i + 2]) for i in range(len(clean))]
    out: list[str] = []
    logs: list[dict] = []

    def add(code: str, window: str, reason: str) -> None:
        if code in known or code in out:
            return
        out.append(code)
        logs.append({"original": window[:120], "normalized": code,
                     "type": "fuzzy_repaired", "reason": reason})

    # A) row-rejoin su finestre di 2 celle adiacenti
    for window in windows:
        for fam in profile.get("long_families", []):
            if families is not None and fam["name"] not in families:
                continue
            for m in fam["_extract"].findall(window):
                add(canonical(m), window,
                    f"row-rejoin ({fam['name']}): frammenti ricomposti su "
                    "celle adiacenti, prezzi mascherati (cap MEDIA)")

    # B) prefix-restore su celle singole, coi vincoli del docstring
    for cell in clean:
        masked = cell
        for fam in profile.get("long_families", []):
            masked = fam["_extract"].sub(" ", masked)
        for fam in profile.get("long_families", []):
            if families is not None and fam["name"] not in families:
                continue
            pref = fam.get("restore_prefix")
            if not pref:
                continue
            for run in DIGIT_RUN.finditer(masked):
                groups = run.group().split()
                if len(groups) > 1 and len(set(groups)) == 1:
                    continue      # eco di colonna, non un codice
                rr = fam.get("_restore_run")
                if rr and not rr.fullmatch(" ".join(groups)):
                    continue      # non ha la forma stampata della famiglia
                cand = pref + canonical(run.group())
                if fam["_token"].fullmatch(cand):
                    add(cand, cell,
                        f"prefix-restore ({fam['name']}): prefisso {pref!r} "
                        "restaurato su run numerico orfano (cap MEDIA)")
    return out, logs
