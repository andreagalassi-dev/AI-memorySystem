#!/usr/bin/env python3
"""Aggiunta interattiva / CLI di un nuovo Profilo Brand (Fase C2).

Permette di inserire un nuovo profilo in PostgreSQL, verificando le regex
di estrazione e normalizzazione su codici di prova forniti.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.postgres_client import get_pg_conn
from src.registry.ingestion_profiles import load_profiles, set_doc_map, upsert_profile


def main() -> int:
    parser = argparse.ArgumentParser(description="Aggiunge un profilo brand.")
    parser.add_argument("--name", required=True, help="Nome del brand (es. 'bosch')")
    parser.add_argument("--glob", required=True, help="Pattern file (es. 'Bosch_*')")
    parser.add_argument("--family-name", default="main", help="Nome famiglia codici")
    parser.add_argument("--extract", required=True, help="Regex estrazione (es. '(?i)BOSCH[A-Z0-9]{5}')")
    parser.add_argument("--token", required=True, help="Regex canonica (es. '^BOSCH[A-Z0-9]{5}$')")
    parser.add_argument("--assembly", default=None, help="Regex opzionale per identificare Assembly/KIT")
    parser.add_argument("--test-codes", nargs="+", default=[], help="Codici di prova su cui testare le regex")
    parser.add_argument("--yes", action="store_true", help="Salta la conferma interattiva dopo il test")

    args = parser.parse_args()

    print(f"--- Configurazione Profilo: {args.name} ---")
    print(f"Glob pattern: {args.glob}")
    print(f"Extract pattern: {args.extract}")
    print(f"Token pattern: {args.token}")
    print(f"Assembly pattern: {args.assembly}")
    print()

    # Valida la sintassi regex
    try:
        re_ext = re.compile(args.extract)
        re_tok = re.compile(args.token)
        re_ass = re.compile(args.assembly) if args.assembly else None
    except re.error as e:
        print(f"ERRORE: Regex invalida fornita -> {e}")
        return 1

    # Testa i codici
    if args.test_codes:
        print("--- Test Regex su Codici Forniti ---")
        all_passed = True
        for code in args.test_codes:
            # test extract (cerca tutte le occorrenze)
            found = re_ext.findall(code)
            # test token (match intero)
            is_token_valid = bool(re_tok.fullmatch(code))

            print(f"Codice '{code}':")
            print(f"  - Extract trovati: {found}")
            print(f"  - Token canonico valido: {is_token_valid}")

            if not found or not is_token_valid:
                all_passed = False
        print()
        if not all_passed:
            print("ATTENZIONE: alcuni codici di test non hanno passato la verifica.")
            if not args.yes:
                resp = input("Vuoi procedere comunque col salvataggio? [y/N] ")
                if resp.lower() not in ('y', 'yes'):
                    print("Operazione annullata.")
                    return 1

    if not args.yes and not args.test_codes:
        resp = input("Vuoi salvare il profilo su database? [y/N] ")
        if resp.lower() not in ('y', 'yes'):
            print("Operazione annullata.")
            return 1

    # Costruisci dizionario profilo
    profile_data = {
        "long_families": [
            {
                "name": args.family_name,
                "extract_pattern": args.extract,
                "token_pattern": args.token,
                "rejoin": False,
                "reason_rejoin": f"codice {args.name}: cleanup esatto",
                "reason_double": f"doppio codice {args.name} in cella: split"
            }
        ],
        "assembly_pattern": args.assembly,
    }

    try:
        with get_pg_conn() as conn:
            # Salvataggio profilo
            pid = upsert_profile(conn, args.name, profile_data)
            
            # Recupera doc_map corrente e aggiorna
            cfg = load_profiles(conn)
            doc_map = cfg["doc_map"]
            
            # Se doc_map ha già una entry che punta ad altro profilo, sovrascrivila col nuovo
            doc_map[args.glob] = args.name
            
            set_doc_map(conn, doc_map)
            
            print(f"OK: Profilo '{args.name}' (ID {pid}) inserito e associato al pattern '{args.glob}'.")
            return 0
    except Exception as e:
        print(f"ERRORE di salvataggio a DB: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(main())
