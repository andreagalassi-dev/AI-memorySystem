"""Esporta la distinta base (BOM) di un assembly.

Usage:
    python scripts/export_bom.py --kit <codice_assembly> [--role tecnico] [--format csv|json]
"""

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.backend.clients.qdrant_client import get_qdrant_client
from src.retrieval.bom import get_bom
from src.common.roles import Role

def main():
    parser = argparse.ArgumentParser(description="Esporta la BOM di un kit/assembly")
    parser.add_argument("--kit", required=True, help="Codice dell'assembly")
    parser.add_argument("--role", default="tecnico", choices=[r.value for r in Role], help="Ruolo utente")
    parser.add_argument("--format", default="csv", choices=["csv", "json"], help="Formato di esportazione")
    parser.add_argument("--out", default=None, help="File di output. Se non specificato, stampa a video")
    
    args = parser.parse_args()
    try:
        role = Role(args.role)
    except ValueError:
        print(f"Errore: ruolo '{args.role}' non valido.", file=sys.stderr)
        return 1
    
    print(f"Recupero BOM per il kit '{args.kit}' con ruolo '{role.value}'...", file=sys.stderr)
    
    try:
        client = get_qdrant_client()
        bom = get_bom(client, args.kit, role)
    except Exception as e:
        print(f"Errore di connessione o recupero DB: {e}", file=sys.stderr)
        return 1
    
    if not bom:
        print(f"ATTENZIONE: Nessun componente trovato per l'assembly {args.kit}.", file=sys.stderr)
        return 1
        
    print(f"Trovati {len(bom)} componenti.", file=sys.stderr)
    
    if args.format == "csv":
        out_f = open(args.out, "w", newline="", encoding="utf-8") if args.out else sys.stdout
        try:
            writer = csv.DictWriter(out_f, fieldnames=["code", "description", "quantity", "page", "source"])
            writer.writeheader()
            writer.writerows(bom)
        finally:
            if args.out:
                out_f.close()
    else:
        out_f = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
        try:
            json.dump(bom, out_f, indent=2, ensure_ascii=False)
            if not args.out:
                print("", file=sys.stdout) # newline at end
        finally:
            if args.out:
                out_f.close()
                
    if args.out:
        print(f"Esportazione completata su: {args.out}", file=sys.stderr)
        
    return 0

if __name__ == "__main__":
    sys.exit(main())
