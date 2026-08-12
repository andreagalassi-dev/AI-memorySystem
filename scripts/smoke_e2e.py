import sys
import time
import subprocess
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Il .env PRIMA di qualunque import che legga l'ambiente: senza, lo smoke gira
# in una configurazione diversa dalla produzione e non lo dichiara.
from src.common.run_config import (  # noqa: E402
    effective_config,
    format_config_banner,
    load_runtime_env,
)

load_runtime_env()

from src.graph.pipeline import answer  # noqa: E402

def preflight():
    print("--- PREFLIGHT ---")
    print(format_config_banner(effective_config()))
    print(
        "NOTA: smoke_e2e chiama answer() SENZA deps -> build_default_deps.\n"
        "      Il ramo visivo gira quindi SEMPRE senza reranker e senza\n"
        "      visual_score_floor, qualunque cosa dica AIKE_VISUAL_RERANK:\n"
        "      i singleton di src/serving/session_deps.py non sono coinvolti.\n"
    )
    subprocess.run(["ollama", "ps"])
    
    # Check RAM usage of python processes
    pid = os.getpid()
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,rss,command"]).decode()
        for line in out.splitlines()[1:]:
            parts = line.strip().split(maxsplit=2)
            if len(parts) >= 3:
                p_pid = int(parts[0])
                rss_kb = int(parts[1])
                cmd = parts[2]
                if 'python' in cmd.lower() and p_pid != pid:
                    if rss_kb > 1024 * 1024: # 1 GB
                        print(f"ERRORE: Processo Python {p_pid} occupa > 1GB ({rss_kb/1024:.2f}MB).")
                        sys.exit(1)
    except Exception as e:
        print(f"Avviso: check processi fallito - {e}")
        
    print("Preflight OK.\n")

def run_queries():
    queries = [
        {
            "name": "CODICE",
            "query": "codice GM41013",
            "role": "tecnico",
            "check": lambda res: (
                res.get("final_level") == "ALTA" and
                res.get("guardrail_gate", "PASS") == "PASS" and
                any(c[0] == "kohler_marine_tp6954" and c[1] == 95 for c in res.get("citations", []))
            )
        },
        {
            "name": "VISIVA",
            "query": "exploded view of the Gehl foot valve",
            "role": "tecnico",
            "check": lambda res: (
                any(c[0] == "gehl_v270_parts" and c[1] == 167 for c in res.get("citations", []))
            )
        },
        {
            "name": "NATURALE",
            "query": "chiusura di scarico con la retina per i capelli, diametro 72",
            "role": "tecnico",
            "check": lambda res: (
                res.get("final_level") in ["MEDIA", "ALTA"] and
                "non ho evidenza" not in res.get("final_answer", "").lower() and
                any(c[0] == "Mamoli_Catalogo_Ricambi_2021_IT_EN" and c[1] == 133 for c in res.get("citations", []))
            )
        },
        {
            "name": "FUORI CORPUS",
            "query": "Qual è la capitale della Francia?",
            "role": "tecnico",
            "check": lambda res: (
                "non ho evidenza" in res.get("final_answer", "").lower() and
                len(res.get("citations", [])) == 0
            )
        }
    ]

    all_pass = True
    print("--- ESECUZIONE QUERY ---")
    for q in queries:
        print(f"\nEseguo query [{q['name']}]: {q['query']}")
        start = time.time()
        try:
            res = answer(q["query"], q["role"])
        except Exception as e:
            res = {"error": str(e)}
        lat = time.time() - start
        
        passed = False
        if "error" not in res:
            try:
                passed = q["check"](res)
            except Exception as e:
                passed = False
                res["check_error"] = str(e)
                
        if not passed:
            all_pass = False
        
        status = "PASS" if passed else "FAIL"
        conf = res.get("final_level", "N/A")
        cits = res.get("citations", [])
        
        print(f"Esito: {status}")
        print(f"Confidence: {conf}")
        print(f"Fonti: {cits}")
        print(f"Latenza: {lat:.2f}s")
        if not passed:
            print(f"Dettagli RAW: {res}")
            
    print("\n--- RIEPILOGO FINALE ---")
    if all_pass:
        print("4/4 PASS. Tutti i criteri soddisfatti.")
        sys.exit(0)
    else:
        print("ALMENO UN FAIL. Criteri non soddisfatti.")
        sys.exit(1)

if __name__ == "__main__":
    preflight()
    run_queries()
