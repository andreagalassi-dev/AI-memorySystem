# Schema Golden Dataset v1

Ogni riga del file `golden_v1.jsonl` rappresenta una singola query valutativa.
Il formato è JSON Lines.

## Schema
```json
{
  "qa_id": "...", 
  "query": "...", 
  "lang": "IT|EN|DE|ES",
  "intent": "code|visual|narrative|refusal",
  "role": "<ruolo RBAC per la query>",
  "expected": { 
      // per code: {"doc", "code", "page"} (+ "value" se noto);
      // per visual: {"doc", "pages": [...]};
      // per narrative: {"doc", "page"} o codice atteso;
      // per refusal: {} 
  },
  "expected_level_demo_era": "ALTA|MEDIA|BASSA|REFUSAL",
  "notes": "..." 
}
```
