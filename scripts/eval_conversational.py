"""Epica F.1c — Eval del contesto conversazionale (multi-turno).

Misura le due cose che contano per il principio non negoziabile:
  1. INVENTION RATE (deve essere 0): nessun identificatore nella query risolta
     che non sia presente nel dialogo (guardia M1 a livello di sistema).
  2. NESSUN confident-wrong: sui casi comparativi "killer" il sistema NON deve
     restituire ALTA su un codice inventato.
Piu' i controlli di risoluzione/topic-switch/no-op/parita per lingua.

Uso: env -u PYTHONPATH OLLAMA_HOST=http://localhost:11434 PYTHONPATH=. \
     .venv/bin/python scripts/eval_conversational.py
"""
from __future__ import annotations
import json, re, sys
from pathlib import Path
sys.path.insert(0, ".")
from src.common.roles import Role
from src.serving.session_deps import get_deps
from src.graph.pipeline import answer
from src.graph.context import ContextAssembler
from src.retrieval.router import TOKEN_RE, _HAS_DIGIT

EVAL = Path("data/eval/eval_conversational.jsonl")
_RESOLVED_RE = re.compile(r"resolved=(['\"])(.*?)\1 changed=")

def identifiers(text: str) -> set[str]:
    return {t for t in TOKEN_RE.findall(text or "")
            if len(t) >= 2 and _HAS_DIGIT.search(t) and "," not in t}

def resolved_from_audit(res: dict, fallback: str) -> str:
    for line in res.get("audit", []):
        m = _RESOLVED_RE.search(line)
        if m:
            return m.group(2)
    return fallback

def main() -> int:
    deps = get_deps(Role.TECNICO)
    cases = [json.loads(l) for l in EVAL.open(encoding="utf-8")]
    invented_total = 0
    fails = []
    print(f"{'case':8}{'lang':5}{'tipo':22}{'intent':11}{'level':7} esito")
    for c in cases:
        ctx = ContextAssembler().build(c["turns"], role=Role.TECNICO, window=6)
        res = answer(c["followup"], Role.TECNICO, deps=deps, context=ctx)
        resolved = resolved_from_audit(res, c["followup"])
        intent = res.get("intent", "?"); level = res.get("final_level", "?")
        # storia = tutti i content dei turni + followup
        hist = " ".join(t.get("content", "") for t in c["turns"]) + " " + c["followup"]
        invented = identifiers(resolved) - identifiers(hist)
        invented_total += len(invented)
        ok = True; why = []
        if invented:
            ok = False; why.append(f"INVENTATO {invented}")
        t = c["tipo"]
        if t.startswith("risoluzione") or t == "risoluzione-cross-lingua":
            ent = c.get("atteso_entita_in_resolved", "")
            if ent and ent.lower() not in resolved.lower():
                ok = False; why.append(f"entita '{ent}' assente dal resolved")
        if t == "topic-switch":
            bad = c.get("entita_da_NON_ereditare", "")
            if bad and bad.lower() in resolved.lower():
                ok = False; why.append(f"ereditata entita vecchia '{bad}'")
        if c.get("atteso_resolved_invariato"):
            if resolved.strip().lower() != c["followup"].strip().lower():
                ok = False; why.append("resolved NON invariato")
        if t.startswith("comparativo"):
            # KILLER: nessun codice inventato (gia' sopra) E non ALTA su structured inventato
            if intent == "structured" and level == "ALTA" and invented:
                ok = False; why.append("CONFIDENT-WRONG: ALTA su codice inventato")
        tag = "OK" if ok else "FAIL: " + "; ".join(why)
        if not ok:
            fails.append(c["case_id"])
        print(f"{c['case_id']:8}{c['lang']:5}{t:22}{intent:11}{level:7} {tag}")
        print(f"         resolved={resolved!r}")
    print(f"\nINVENTION TOTALE (deve essere 0): {invented_total}")
    print(f"CASI FALLITI: {fails if fails else 'nessuno'}  ({len(cases)-len(fails)}/{len(cases)})")
    return 1 if (invented_total or fails) else 0

if __name__ == "__main__":
    sys.exit(main())
