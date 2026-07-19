"""P5.1 — Script di migrazione per aggiungere l'indice payload su parent_id.

Crea in modo idempotente il payload index KEYWORD sul campo `parent_id` 
nella collezione `aike_chunks`. Indispensabile per evitare scan O(N) nell'Hop 2.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qdrant_client import models  # noqa: E402
from src.backend.clients.qdrant_client import get_qdrant_client  # noqa: E402
from src.ingestion.embedding import COLLECTION  # noqa: E402

def main() -> int:
    client = get_qdrant_client()
    
    print(f"Creazione indice payload su 'parent_id' per la collezione {COLLECTION}...")
    client.create_payload_index(
        collection_name=COLLECTION,
        field_name="parent_id",
        field_schema=models.PayloadSchemaType.KEYWORD,
        wait=True
    )
    
    print("Indice creato con successo. Operazione completata.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
