import argparse
import sys
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.common.roles import Role
from src.agent.quote_draft import generate_quote_draft

def main() -> int:
    parser = argparse.ArgumentParser(description="Genera una bozza di preventivo")
    parser.add_argument("--role", default="tecnico", help="Ruolo utente")
    parser.add_argument("--file", help="File di testo con una richiesta per riga")
    parser.add_argument("requests", nargs="*", help="Richieste testuali")
    
    args = parser.parse_args()
    
    try:
        role = Role(args.role)
    except ValueError:
        print(f"Ruolo non valido: {args.role}")
        return 1
        
    reqs = []
    if args.file:
        with open(args.file, "r") as f:
            reqs.extend(line.strip() for line in f if line.strip())
            
    if args.requests:
        reqs.extend(args.requests)
        
    if not reqs:
        print("Nessuna richiesta fornita. Specificare --file o richieste testuali.")
        return 1
        
    print(f"Generazione preventivo per {len(reqs)} richieste...", file=sys.stderr)
    draft = generate_quote_draft(reqs, role)
    
    writer = csv.DictWriter(sys.stdout, fieldnames=["richiesta", "codice_proposto", "descrizione", "confidenza", "fonte", "status"])
    writer.writeheader()
    writer.writerows(draft)
    
    return 0

if __name__ == "__main__":
    sys.exit(main())
