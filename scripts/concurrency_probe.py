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
        deps = get_deps(role)
        res = answer(query, role=role, deps=deps)
        citations = res.get("citations", [])
        results[query] = {
            "status": "OK",
            "level": res.get("final_level"),
            "citations": citations,
            "error": None,
            "audit": res.get("audit_trail", [])
        }
        print(f"[{tid}] DONE: '{query}'", flush=True)
    except Exception as e:
        results[query] = {
            "status": "ERROR",
            "level": None,
            "citations": [],
            "error": repr(e),
            "audit": []
        }
        print(f"[{tid}] EXCEPTION: {repr(e)}", flush=True)

def check_assert(condition, msg, query):
    if not condition:
        print(f"❌ ASSERTION FAILED: {msg}")
        r = results[query]
        print(f"  Audit trail per '{query}':")
        for step in r.get("audit", []):
            print(f"    - {step}")
        sys.exit(1)

def main():
    print("=== PROBE CONCORRENZA ===", flush=True)
    print_ram("INITIAL")
    
    q_gm41013 = "codice GM41013"
    q_415869 = "codice 415869"
    q_gehl = "exploded view of the Gehl foot valve"
    q_nordcap = "Explosionszeichnung der NordCap Kühltruhe"
    q_olio = "Come si cambia l'olio della pompa?"
    q_garanzia = "Quali sono i termini di garanzia?"

    t1 = threading.Thread(target=run_query, args=("tecnico", q_gm41013), name="Codice-1")
    t2 = threading.Thread(target=run_query, args=("tecnico", q_415869), name="Codice-2")
    
    print("\n--- TEST CONCORRENZA CODICE ---", flush=True)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    t3 = threading.Thread(target=run_query, args=("tecnico", q_gehl), name="Visiva-Tec")
    t4 = threading.Thread(target=run_query, args=("commerciale", q_nordcap), name="Visiva-Com")
    
    print("\n--- TEST CONCORRENZA VISIVA ---", flush=True)
    t3.start()
    t4.start()
    t3.join()
    t4.join()

    t5 = threading.Thread(target=run_query, args=("tecnico", q_olio), name="Nat-Tec")
    t6 = threading.Thread(target=run_query, args=("commerciale", q_garanzia), name="Nat-Com")

    import time
    print("\n--- TEST CONCORRENZA NATURALE ---", flush=True)
    start_time = time.time()
    t5.start()
    t6.start()
    t5.join()
    t6.join()
    end_time = time.time()
    print(f"Tempo totale naturale: {end_time - start_time:.2f}s (atteso serializzati)", flush=True)
    
    print("\n=== RISULTATI ===", flush=True)
    for q, r in results.items():
        print(f"Query: '{q}'", flush=True)
        print(f"  Esito: {r['status']}", flush=True)
        print(f"  Livello: {r['level']}", flush=True)
        print(f"  Citazioni: {r['citations']}", flush=True)
        print(f"  Eccezione: {r['error']}", flush=True)
    
    print_ram("FINAL")

    # Asserzioni:
    # GM41013 -> ALTA kohler p95 CON citazioni
    r1 = results[q_gm41013]
    check_assert(r1["status"] == "OK" and r1["level"] == "ALTA" and len(r1["citations"]) > 0, "GM41013 fallita", q_gm41013)
    check_assert(any("kohler" in str(c).lower() or "p95" in str(c).lower() for c in r1["citations"]), "GM41013: manca kohler/p95", q_gm41013)
    
    # 415869(tecnico) -> ALTA manzoni p45 CON citazioni
    r2 = results[q_415869]
    check_assert(r2["status"] == "OK" and r2["level"] == "ALTA" and len(r2["citations"]) > 0, "415869 fallita", q_415869)
    check_assert(any("manzoni" in str(c).lower() or "p45" in str(c).lower() for c in r2["citations"]), "415869: manca manzoni/p45", q_415869)
    
    # Gehl -> p167 tra le citazioni
    r3 = results[q_gehl]
    check_assert(r3["status"] == "OK" and any("167" in str(c) for c in r3["citations"]), "Gehl: manca p167", q_gehl)
    
    # NordCap -> cit non vuote
    r4 = results[q_nordcap]
    check_assert(r4["status"] == "OK" and len(r4["citations"]) > 0, "NordCap: cit vuote", q_nordcap)
    
    # garanzia -> risposta con citazioni NON vuote
    r6 = results[q_garanzia]
    check_assert(r6["status"] == "OK" and len(r6["citations"]) > 0, "garanzia: cit vuote", q_garanzia)
    
    # "olio della pompa" -> esito onesto qualunque (no eccezioni)
    r5 = results[q_olio]
    check_assert(r5["status"] == "OK", "olio pompa: fallito con eccezione", q_olio)
    
    print("✅ Tutte le asserzioni sono passate.")
    sys.exit(0)

if __name__ == "__main__":
    main()
