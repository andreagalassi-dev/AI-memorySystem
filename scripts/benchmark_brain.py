#!/usr/bin/env python3
"""Benchmark cervello A/B/D — confronto backend LLM sul grafo AIKE.

Selezione backend via env AIKE_BENCH_BACKEND:
  - mlx              : candidato A — MLXModel + FallbackLLM (stato attuale)
  - ollama:gemma4:26b-mlx  : candidato B — Ollama con gemma4:26b-mlx (MTP)
  - ollama:qwen2.5:14b     : controllo D — Ollama con qwen2.5:14b

Ogni query viene eseguita 3 volte per misurare stabilità.
Output: tabella riassuntiva per backend (stdout).

NESSUNA modifica a src/**: il benchmark inietta l'LLM scelto in build_default_deps.
"""
from __future__ import annotations

import json
import logging
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.common.roles import Role, parse_role
from src.graph.pipeline import answer, build_default_deps, build_graph, Evidence

# ---------------------------------------------------------------------------
# Query set fisso (dal file 14 — query collaudate)
# ---------------------------------------------------------------------------
QUERIES = [
    # --- 4 query naturali ---
    {
        "id": "NAT-1", "classe": "naturale",
        "query": "il D906969ZR è il filtro dell'aria della ventola?",
        "role": "tecnico",
        "fonte_attesa": ("Silca_TriaxPro_Ricambi_Multilingua", 3),
    },
    {
        "id": "NAT-2", "classe": "naturale",
        "query": "chiusura di scarico con la retina per i capelli, diametro 72",
        "role": "tecnico",
        "fonte_attesa": ("Mamoli_Catalogo_Ricambi_2021_IT_EN", 133),
    },
    {
        "id": "NAT-3", "classe": "naturale",
        "query": "vite testa svasata cinque per trenta zincata scura",
        "role": "tecnico",
        "fonte_attesa": ("manzoni_comac_spareparts_B", 14),
    },
    {
        "id": "NAT-4", "classe": "naturale",
        "query": "spia trasparente per controllare l'olio della centralina SCV",
        "role": "tecnico",
        "fonte_attesa": None,  # fonti multiple (arol/oleodinamica), no single check
    },
    # --- 1 query tecnica fuori corpus ---
    {
        "id": "TEC-1", "classe": "tecnica",
        "query": "Quali sono i termini di garanzia?",
        "role": "tecnico",
        "fonte_attesa": None,  # atteso rifiuto
    },
    # --- 3 query codice ---
    {
        "id": "COD-1", "classe": "codice",
        "query": "codice GM41013",
        "role": "tecnico",
        "fonte_attesa": ("kohler_marine_tp6954", 95),
    },
    {
        "id": "COD-2", "classe": "codice",
        "query": "codice 415869",
        "role": "tecnico",
        "fonte_attesa": ("manzoni_comac_spareparts_A", 45),
    },
    {
        "id": "COD-3", "classe": "codice",
        "query": "codice 14576",
        "role": "tecnico",
        "fonte_attesa": ("Mamoli_Catalogo_Ricambi_2021_IT_EN", 100),
    },
]

RUNS_PER_QUERY = 3

# ---------------------------------------------------------------------------
# Configurazione logging — cattura "Backend MLX fallito" nei log
# ---------------------------------------------------------------------------

class FallbackCounter(logging.Handler):
    """Conta le righe 'Backend MLX fallito' emesse da FallbackLLM."""
    def __init__(self):
        super().__init__()
        self.count = 0

    def emit(self, record):
        msg = self.getMessage(record) if hasattr(record, 'getMessage') else str(record.msg)
        try:
            msg = record.getMessage()
        except Exception:
            msg = str(record.msg)
        if "Backend MLX fallito" in msg:
            self.count += 1

    def reset(self):
        self.count = 0


# ---------------------------------------------------------------------------
# Costruzione LLM per backend
# ---------------------------------------------------------------------------

def build_llm(backend: str):
    """Costruisce l'LLM in base al backend selezionato. Ritorna (llm, label)."""
    if backend == "mlx":
        # Stato attuale: MLXModel + FallbackLLM (usa config.settings)
        from src.graph.llm import MLXModel, FallbackLLM
        from src.serving.multiplexer import ModelMultiplexer, MlxModelRuntime
        from src.backend.clients.ollama_client import OllamaModel
        from src.backend.config import settings

        mlx_runtime = MlxModelRuntime(default_num_ctx=settings.local_ctx_cap)
        multiplexer = ModelMultiplexer(mlx_runtime, max_resident=1,
                                       ctx_cap=settings.local_ctx_cap)
        primary = MLXModel(settings.logic_model, multiplexer)
        fallback = OllamaModel()
        llm = FallbackLLM(primary, fallback)
        return llm, f"A (MLX {settings.logic_model})"

    elif backend.startswith("ollama:"):
        model_name = backend.split(":", 1)[1]
        from src.backend.clients.ollama_client import OllamaModel
        
        # B (gemma4 via ollama) necessita di disattivare il thinking per non consumare il budget num_predict
        think_flag = False if model_name == "gemma4:26b-mlx" else None
        llm = OllamaModel(model=model_name, think=think_flag)
        
        label_map = {
            "gemma4:26b-mlx": "B (Ollama gemma4:26b-mlx)",
            "qwen2.5:14b": "D (Ollama qwen2.5:14b)",
        }
        return llm, label_map.get(model_name, f"Ollama {model_name}")
    else:
        print(f"ERRORE: backend sconosciuto: {backend}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Esecuzione query
# ---------------------------------------------------------------------------

def run_single(graph, query_spec: dict, fallback_counter: FallbackCounter) -> dict:
    """Esegue una query sul grafo e raccoglie le metriche."""
    fallback_counter.reset()
    q = query_spec["query"]
    role = query_spec["role"]

    t0 = time.time()
    exc_text = None
    try:
        result = graph.invoke({"query": q, "role": role})
    except Exception as e:
        result = {"final_level": "ERRORE", "final_answer": str(e),
                  "citations": [], "guardrail_gate": "N/A", "audit": []}
        exc_text = f"{type(e).__name__}: {e}"
    latency = time.time() - t0

    level = result.get("final_level", "N/A")
    answer_text = result.get("final_answer", "")
    citations = result.get("citations", [])
    guardrail = result.get("guardrail_gate", "PASS")

    # Fonte attesa citata?
    fonte_attesa = query_spec.get("fonte_attesa")
    fonte_ok = None
    if fonte_attesa is not None:
        doc, page = fonte_attesa
        fonte_ok = any(c[0] == doc and c[1] == page for c in citations)

    # Citazioni vuote?
    cit_vuote = len(citations) == 0

    # Rifiuto/blocco guardrail
    from src.retrieval.synthesis import REFUSAL
    rifiuto = (REFUSAL.lower() in answer_text.lower()) or guardrail == "BLOCK"

    return {
        "id": query_spec["id"],
        "classe": query_spec["classe"],
        "query": q,
        "run_level": level,
        "fonte_ok": fonte_ok,
        "cit_vuote": cit_vuote,
        "rifiuto": rifiuto,
        "guardrail": guardrail,
        "latenza_s": round(latency, 2),
        "fallback_count": fallback_counter.count,
        "eccezione": exc_text,
        "answer_preview": answer_text[:200],
        "n_citations": len(citations),
        "citations_raw": [(c[0], c[1]) for c in citations],
    }


def preflight():
    """Stampa ollama ps e RAM check."""
    print("=" * 70)
    print("PREFLIGHT")
    print("=" * 70)
    try:
        subprocess.run(["ollama", "ps"], check=False)
    except Exception as e:
        print(f"ollama ps fallito: {e}")

    pid = os.getpid()
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,rss,command"]).decode()
        big = []
        for line in out.splitlines()[1:]:
            parts = line.strip().split(maxsplit=2)
            if len(parts) >= 3:
                rss_kb = int(parts[1])
                cmd = parts[2]
                if "python" in cmd.lower() and int(parts[0]) != pid:
                    if rss_kb > 500_000:  # > 500MB
                        big.append(f"  PID {parts[0]}: {rss_kb/1024:.0f} MB — {cmd[:80]}")
        if big:
            print("ATTENZIONE: processi Python residenti pesanti:")
            for b in big:
                print(b)
        else:
            print("RAM: nessun processo Python pesante rilevato.")
    except Exception as e:
        print(f"RAM check fallito: {e}")
    print()


def print_table(runs: list[dict], label: str):
    """Stampa la tabella riassuntiva per un backend."""
    print()
    print("=" * 90)
    print(f"RISULTATI BACKEND: {label}")
    print("=" * 90)

    # --- Tabella dettaglio per run ---
    hdr = f"{'ID':<7} {'Run':>3} {'Livello':<8} {'FonteOK':>7} {'CitVuote':>8} {'Rifiuto':>7} {'Guard':>6} {'Lat(s)':>7} {'FBack':>5} {'Exc':>4}"
    print(hdr)
    print("-" * len(hdr))
    for r in runs:
        fo = "—" if r["fonte_ok"] is None else ("✓" if r["fonte_ok"] else "✗")
        cv = "✓" if r["cit_vuote"] else "✗"
        rif = "✓" if r["rifiuto"] else "✗"
        exc = "✓" if r["eccezione"] else "—"
        print(f"{r['id']:<7} {r['run_n']:>3} {r['run_level']:<8} {fo:>7} {cv:>8} {rif:>7} {r['guardrail']:>6} {r['latenza_s']:>7.2f} {r['fallback_count']:>5} {exc:>4}")

    print()

    # --- Tabella riassuntiva aggregata ---
    # Per query con fonte_attesa: correttezza = fonte_ok is True
    with_fonte = [r for r in runs if r["fonte_ok"] is not None]
    corr_pct = (sum(1 for r in with_fonte if r["fonte_ok"]) / len(with_fonte) * 100) if with_fonte else 0
    cit_presenti = sum(1 for r in runs if not r["cit_vuote"])
    cit_pct = cit_presenti / len(runs) * 100 if runs else 0
    rifiuti_pct = sum(1 for r in runs if r["rifiuto"]) / len(runs) * 100 if runs else 0
    lats = [r["latenza_s"] for r in runs]
    lat_p50 = statistics.median(lats) if lats else 0
    lat_max = max(lats) if lats else 0
    fallbacks = sum(r["fallback_count"] for r in runs)
    total_runs = len(runs)
    fb_pct = fallbacks / total_runs * 100 if total_runs else 0

    print(f"RIEPILOGO {label}:")
    print(f"  Correttezza (fonte attesa citata): {corr_pct:.1f}% ({sum(1 for r in with_fonte if r['fonte_ok'])}/{len(with_fonte)})")
    print(f"  Citazioni presenti:                {cit_pct:.1f}% ({cit_presenti}/{total_runs})")
    print(f"  Rifiuti:                           {rifiuti_pct:.1f}%")
    print(f"  Latenza p50/max:                   {lat_p50:.2f}s / {lat_max:.2f}s")
    print(f"  Fallback MLX:                      {fb_pct:.1f}% ({fallbacks}/{total_runs})")

    # --- Per classe ---
    classi = sorted(set(r["classe"] for r in runs))
    print()
    print(f"  {'Classe':<12} {'Corr%':>6} {'CitPres%':>8} {'Rif%':>6} {'p50':>7} {'Max':>7} {'FBack%':>7}")
    print(f"  {'-'*12} {'-'*6} {'-'*8} {'-'*6} {'-'*7} {'-'*7} {'-'*7}")
    for cls in classi:
        cr = [r for r in runs if r["classe"] == cls]
        wf = [r for r in cr if r["fonte_ok"] is not None]
        c_corr = (sum(1 for r in wf if r["fonte_ok"]) / len(wf) * 100) if wf else 0
        c_cit = sum(1 for r in cr if not r["cit_vuote"]) / len(cr) * 100
        c_rif = sum(1 for r in cr if r["rifiuto"]) / len(cr) * 100
        c_lats = [r["latenza_s"] for r in cr]
        c_p50 = statistics.median(c_lats)
        c_max = max(c_lats)
        c_fb = sum(r["fallback_count"] for r in cr) / len(cr) * 100
        print(f"  {cls:<12} {c_corr:>6.1f} {c_cit:>8.1f} {c_rif:>6.1f} {c_p50:>7.2f} {c_max:>7.2f} {c_fb:>7.1f}")

    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    backend = os.environ.get("AIKE_BENCH_BACKEND", "mlx")
    print(f"\n{'#' * 70}")
    print(f"# BENCHMARK CERVELLO — backend: {backend}")
    print(f"{'#' * 70}\n")

    preflight()

    # Setup logging per catturare fallback
    fallback_counter = FallbackCounter()
    fallback_counter.setLevel(logging.WARNING)
    logging.getLogger("src.graph.llm").addHandler(fallback_counter)
    # Abilita logging per vedere cosa succede
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    # Build LLM
    llm, label = build_llm(backend)
    print(f"Backend LLM costruito: {label}")
    print()

    # Build deps e grafo (ruolo tecnico per tutte le query)
    role = parse_role("tecnico")
    deps = build_default_deps(role, llm=llm)
    graph = build_graph(deps)
    print("Deps e grafo costruiti. Inizio run.\n")

    all_runs = []
    for qi, qspec in enumerate(QUERIES, 1):
        for run_n in range(1, RUNS_PER_QUERY + 1):
            print(f"[{qi}/{len(QUERIES)} run {run_n}/{RUNS_PER_QUERY}] {qspec['id']}: {qspec['query'][:60]}...")
            result = run_single(graph, qspec, fallback_counter)
            result["run_n"] = run_n
            all_runs.append(result)
            print(f"  → {result['run_level']} | lat={result['latenza_s']:.2f}s | cit={result['n_citations']} | fb={result['fallback_count']}")
            if result["eccezione"]:
                print(f"  !! ECCEZIONE: {result['eccezione']}")
            print()

    print_table(all_runs, label)

    # Dump JSON grezzo per il report
    json_path = ROOT / "scripts" / f"benchmark_raw_{backend.replace(':', '_')}.json"
    with open(json_path, "w") as f:
        json.dump(all_runs, f, indent=2, ensure_ascii=False)
    print(f"Dati grezzi salvati in: {json_path}")


if __name__ == "__main__":
    main()
