#!/usr/bin/env python3
import sys
import os
import threading
import psutil

# Preambolo sys.path per importare src
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.serving.session_deps import get_deps
from src.graph.pipeline import answer

def print_ram(label: str):
    process = psutil.Process(os.getpid())
    ram_mb = process.memory_info().rss / 1024 / 1024
    print(f"[{label}] RAM: {ram_mb:.2f} MB", flush=True)

results = {}

def run_query(role: str, query: str):
    tid = threading.current_thread().name
    print(f"[{tid}] START query: '{query}' (ruolo: {role})", flush=True)
    try:
        # Usa get_deps per testare la condivisione esatta usata da Chainlit
        deps = get_deps(role)
        res = answer(query, role=role, deps=deps)
        citations = res.get("citations", [])
        results[query] = {
            "status": "OK",
            "level": res.get("final_level"),
            "citations": citations,
            "error": None
        }
        print(f"[{tid}] DONE: '{query}'", flush=True)
    except Exception as e:
        results[query] = {
            "status": "ERROR",
            "level": None,
            "citations": [],
            "error": repr(e)
        }
        print(f"[{tid}] EXCEPTION: {repr(e)}", flush=True)

def main():
    print("=== PROBE CONCORRENZA ===", flush=True)
    print_ram("INITIAL")
    
    # b. query CODICE concorrenti: "codice GM41013" (tecnico), "codice 415869" (tecnico)
    t1 = threading.Thread(target=run_query, args=("tecnico", "codice GM41013"), name="Codice-1")
    t2 = threading.Thread(target=run_query, args=("tecnico", "codice 415869"), name="Codice-2")
    
    print("\n--- TEST CONCORRENZA CODICE ---", flush=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # c. query VISIVE concorrenti: "Gehl" (tecnico), "NordCap" (commerciale)
    t3 = threading.Thread(target=run_query, args=("tecnico", "Gehl"), name="Visiva-Tec")
    t4 = threading.Thread(target=run_query, args=("commerciale", "NordCap"), name="Visiva-Com")
    
    print("\n--- TEST CONCORRENZA VISIVA ---", flush=True)
    t3.start()
    t4.start()
    t3.join()
    t4.join()

    # d. query NATURALI concorrenti: sintesi MLX
    t5 = threading.Thread(target=run_query, args=("tecnico", "Come si cambia l'olio della pompa?"), name="Nat-Tec")
    t6 = threading.Thread(target=run_query, args=("commerciale", "Quali sono i termini di garanzia?"), name="Nat-Com")

    import time
    print("\n--- TEST CONCORRENZA NATURALE ---", flush=True)
    start_time = time.time()
    t5.start()
    t6.start()
    t5.join()
    t6.join()
    end_time = time.time()
    print(f"Tempo totale naturale: {end_time - start_time:.2f}s (atteso 2x60-110s serializzati)", flush=True)
    
    
    print("\n=== RISULTATI ===", flush=True)
    for q, r in results.items():
        print(f"Query: '{q}'", flush=True)
        print(f"  Esito: {r['status']}", flush=True)
        print(f"  Livello: {r['level']}", flush=True)
        print(f"  Citazioni: {r['citations']}", flush=True)
        print(f"  Eccezione: {r['error']}", flush=True)
    
    print_ram("FINAL")

if __name__ == "__main__":
    main()
