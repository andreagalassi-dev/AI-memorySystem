# Eval Ampio del Motore — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
> **NB progetto:** in questo repo l'implementazione è affidata a un ESECUTORE separato via l'utente; il reviewer verifica ogni task coi tool. I task qui sotto diventano prompt numerati, uno alla volta.

**Goal:** Costruire il *metro ampio* del motore — un runner read-only che misura recall@k, correttezza-data-l'evidenza, tasso di "non lo so" e correttezza di routing, separati per lingua e per ramo, più una coverage table dei 142 PDF.

**Architecture:** Pacchetto `src/eval/` con pezzi isolati: schema+loader del set di verità (`truth_set.py`), metriche pure (`metrics.py`), runner+aggregazione (`runner.py`), check auto-derivati+copertura (`auto_checks.py`), CLI (`scripts/eval_ampio.py`). Il runner riusa `answer()`/`get_deps` come `golden_gate`, non tocca la produzione.

**Tech Stack:** Python 3.11, `.venv`, psycopg (Postgres), qdrant via HTTP, pytest.

## Global Constraints

- Ambiente: `env -u PYTHONPATH ... PYTHONPATH=. .venv/bin/python` (mai `python` nudo).
- Chiamate al modello (solo Task 5): `OLLAMA_HOST=http://localhost:11435`; leggere il LOG, non l'exit code.
- READ-ONLY sulla produzione: nessun INSERT/UPDATE/DELETE su `estrazione`/Qdrant/`aike_chunks`.
- TDD rosso-prima obbligatorio; commit piccoli; `python scripts/golden_gate.py` deve restare verde.
- Lingue calcolate SEMPRE separate (it/en/de/es). Due metriche mai fuse: recall@k ≠ correttezza.
- Firme reali del motore (da `src/graph/pipeline.py`): `Evidence(doc,page,lang,code,desc,text,qty,price)`; stato `GraphState` con chiavi `evidence: list[Evidence]`, `visual_pages: list[PageHit]`, `citations: list[tuple[str,int]]`, `route: str`, `intent: str` (valori `STRUCTURED|VISUAL|NARRATIVE`), `final_level: str`, `final_answer: str`, `answer: str|None`. `PageHit(doc,page,score,n_tiles)` da `src/retrieval/pixelrag/index.py`. `answer(query, role, deps=...)` ritorna il dict di stato.

---

### Task 1: Schema e loader del set di verità

**Files:**
- Create: `src/eval/__init__.py` (vuoto)
- Create: `src/eval/truth_set.py`
- Create: `output/eval/goldenset_v2/sample_truth.json` (fixture minima per i test)
- Test: `tests/eval/test_truth_set.py`

**Interfaces:**
- Produces:
  - `EvalItem` (dataclass frozen) campi: `id:str, query:str, lang:str, branch:str, role:str, doc:str|None, page:int|None, expected:str|None, kind:str, must_say_nonso:bool, source:str`.
  - `load_truth_set(path:str) -> list[EvalItem]` — legge JSON `{"items":[...]}`, salta le voci con chiave `_section` (commenti), valida e costruisce gli `EvalItem`.
  - `ValidationError` (sottoclasse di `ValueError`) sollevata su item malformati.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_truth_set.py
import json, pytest
from src.eval.truth_set import EvalItem, load_truth_set, ValidationError

def _write(tmp_path, items):
    p = tmp_path / "t.json"
    p.write_text(json.dumps({"items": items}))
    return str(p)

def test_load_valid_item(tmp_path):
    path = _write(tmp_path, [
        {"id":"S1","query":"codice X","lang":"it","branch":"structured",
         "role":"tecnico","doc":"d","page":2,"expected":"78.83",
         "kind":"positive","must_say_nonso":False,"source":"crop"},
        {"_section":"commento da ignorare"},
    ])
    items = load_truth_set(path)
    assert len(items) == 1
    assert isinstance(items[0], EvalItem)
    assert items[0].branch == "structured" and items[0].page == 2

def test_bad_branch_rejected(tmp_path):
    path = _write(tmp_path, [
        {"id":"S1","query":"q","lang":"it","branch":"WRONG","role":"tecnico",
         "doc":None,"page":None,"expected":None,"kind":"negative",
         "must_say_nonso":True,"source":"crop"}])
    with pytest.raises(ValidationError):
        load_truth_set(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/pytest tests/eval/test_truth_set.py -v`
Expected: FAIL (module `src.eval.truth_set` non esiste).

- [ ] **Step 3: Write minimal implementation**

```python
# src/eval/truth_set.py
from __future__ import annotations
import json
from dataclasses import dataclass

class ValidationError(ValueError):
    pass

_BRANCHES = {"structured", "visual", "narrative"}
_LANGS = {"it", "en", "de", "es"}
_KINDS = {"positive", "negative"}

@dataclass(frozen=True)
class EvalItem:
    id: str
    query: str
    lang: str
    branch: str
    role: str
    doc: str | None
    page: int | None
    expected: str | None
    kind: str
    must_say_nonso: bool
    source: str

def _build(raw: dict) -> EvalItem:
    try:
        item = EvalItem(
            id=str(raw["id"]), query=str(raw["query"]), lang=str(raw["lang"]),
            branch=str(raw["branch"]), role=str(raw.get("role", "tecnico")),
            doc=raw.get("doc"), page=raw.get("page"), expected=raw.get("expected"),
            kind=str(raw.get("kind", "positive")),
            must_say_nonso=bool(raw.get("must_say_nonso", False)),
            source=str(raw.get("source", "crop")),
        )
    except KeyError as e:
        raise ValidationError(f"campo mancante: {e}") from e
    if item.branch not in _BRANCHES:
        raise ValidationError(f"branch invalido: {item.branch!r}")
    if item.lang not in _LANGS:
        raise ValidationError(f"lang invalida: {item.lang!r}")
    if item.kind not in _KINDS:
        raise ValidationError(f"kind invalido: {item.kind!r}")
    return item

def load_truth_set(path: str) -> list[EvalItem]:
    with open(path) as f:
        data = json.load(f)
    return [_build(r) for r in data["items"] if "_section" not in r]
```

Crea anche `src/eval/__init__.py` vuoto e la fixture `output/eval/goldenset_v2/sample_truth.json`:

```json
{"items":[
  {"id":"S1","query":"codice PD21H100S-F-BL","lang":"it","branch":"structured","role":"tecnico","doc":"pi_pulley_listino_pulegge_2025","page":1,"expected":"78.83","kind":"positive","must_say_nonso":false,"source":"crop"},
  {"id":"NEG1","query":"codice ZZINESISTENTE999","lang":"it","branch":"structured","role":"tecnico","doc":null,"page":null,"expected":null,"kind":"negative","must_say_nonso":true,"source":"crop"}
]}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/pytest tests/eval/test_truth_set.py -v`
Expected: PASS (2 test).

- [ ] **Step 5: Commit**

```bash
git add src/eval/__init__.py src/eval/truth_set.py output/eval/goldenset_v2/sample_truth.json tests/eval/test_truth_set.py
git commit -m "eval(B): schema+loader set di verita (EvalItem, load_truth_set)"
```

---

### Task 2: Metriche pure (recall@k, correttezza, non-lo-so, routing)

**Files:**
- Create: `src/eval/metrics.py`
- Test: `tests/eval/test_metrics.py`

**Interfaces:**
- Consumes: `EvalItem` da Task 1.
- Produces:
  - `RunOutput` (dataclass): `level:str, answer:str|None, citations:list[tuple[str,int]], evidence:list[tuple[str,int]], visual_pages:list[tuple[str,int]], route:str|None, intent:str|None`.
  - `recall_at_k(item:EvalItem, out:RunOutput, k:int=10) -> bool|None` — `None` se non applicabile (`item.doc` None). structured→`citations`; visual→`visual_pages`; narrative→`evidence`. Hit se `(doc,page)` (o solo `doc` quando `page is None`) è nei primi k.
  - `correctness(item:EvalItem, out:RunOutput) -> bool|None` — `None` se `item.expected` None; altrimenti sottostringa case-insensitive di `expected` in `out.answer`.
  - `nonso_status(out:RunOutput) -> str` — `"nonso"` se `level` upper in `{"BASSA","REFUSAL"}` o `answer` None; altrimenti `"answered"`.
  - `routing_match(item:EvalItem, out:RunOutput) -> bool|None` — confronto `item.branch` con `out.intent` (case-insensitive); `None` se `out.intent` None.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_metrics.py
from src.eval.truth_set import EvalItem
from src.eval.metrics import RunOutput, recall_at_k, correctness, nonso_status, routing_match

def _item(**kw):
    base = dict(id="i", query="q", lang="it", branch="narrative", role="tecnico",
                doc="d", page=5, expected=None, kind="positive",
                must_say_nonso=False, source="crop")
    base.update(kw); return EvalItem(**base)

def _out(**kw):
    base = dict(level="ALTA", answer="x", citations=[], evidence=[], visual_pages=[],
                route="natural", intent="NARRATIVE")
    base.update(kw); return RunOutput(**base)

def test_recall_narrative_hit():
    assert recall_at_k(_item(branch="narrative"), _out(evidence=[("d",5),("e",1)]), k=10) is True

def test_recall_miss_and_notapplicable():
    assert recall_at_k(_item(branch="narrative"), _out(evidence=[("e",1)]), k=10) is False
    assert recall_at_k(_item(doc=None, page=None), _out(), k=10) is None

def test_recall_structured_uses_citations():
    assert recall_at_k(_item(branch="structured"), _out(citations=[("d",5)]), k=10) is True

def test_correctness_substring():
    assert correctness(_item(expected="78.83"), _out(answer="prezzo 78.83 EUR")) is True
    assert correctness(_item(expected="99.00"), _out(answer="prezzo 78.83 EUR")) is False
    assert correctness(_item(expected=None), _out()) is None

def test_nonso_and_routing():
    assert nonso_status(_out(level="BASSA")) == "nonso"
    assert nonso_status(_out(level="ALTA")) == "answered"
    assert routing_match(_item(branch="narrative"), _out(intent="NARRATIVE")) is True
    assert routing_match(_item(branch="visual"), _out(intent="NARRATIVE")) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/pytest tests/eval/test_metrics.py -v`
Expected: FAIL (module `src.eval.metrics` non esiste).

- [ ] **Step 3: Write minimal implementation**

```python
# src/eval/metrics.py
from __future__ import annotations
from dataclasses import dataclass
from src.eval.truth_set import EvalItem

@dataclass
class RunOutput:
    level: str
    answer: str | None
    citations: list[tuple[str, int]]
    evidence: list[tuple[str, int]]
    visual_pages: list[tuple[str, int]]
    route: str | None
    intent: str | None

def _candidates(item: EvalItem, out: RunOutput) -> list[tuple[str, int]]:
    if item.branch == "structured":
        return out.citations
    if item.branch == "visual":
        return out.visual_pages
    return out.evidence

def recall_at_k(item: EvalItem, out: RunOutput, k: int = 10) -> bool | None:
    if item.doc is None:
        return None
    for doc, page in _candidates(item, out)[:k]:
        if doc == item.doc and (item.page is None or page == item.page):
            return True
    return False

def correctness(item: EvalItem, out: RunOutput) -> bool | None:
    if item.expected is None:
        return None
    return item.expected.lower() in (out.answer or "").lower()

def nonso_status(out: RunOutput) -> str:
    if out.answer is None or (out.level or "").upper() in {"BASSA", "REFUSAL"}:
        return "nonso"
    return "answered"

def routing_match(item: EvalItem, out: RunOutput) -> bool | None:
    if not out.intent:
        return None
    return item.branch.lower() == out.intent.lower()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/pytest tests/eval/test_metrics.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eval/metrics.py tests/eval/test_metrics.py
git commit -m "eval(B): metriche pure recall@k/correttezza/non-lo-so/routing"
```

---

### Task 3: Runner e aggregazione per lingua/ramo

**Files:**
- Create: `src/eval/runner.py`
- Test: `tests/eval/test_runner.py`

**Interfaces:**
- Consumes: `EvalItem` (Task 1); `RunOutput`, metriche (Task 2).
- Produces:
  - `to_run_output(state: dict) -> RunOutput` — estrae dallo stato: `evidence`→`[(e.doc,e.page)...]`, `visual_pages`→`[(p.doc,p.page)...]`, `citations`, `final_level` (fallback `level`), `final_answer` (fallback `answer`), `route`, `intent`.
  - `run_eval(items, answer_fn, deps_for_role, k=10) -> EvalReport` dove `answer_fn(query, role, deps) -> dict` e `deps_for_role(role) -> deps`.
  - `EvalReport` (dataclass): `per_lang: dict[str,dict]`, `per_branch: dict[str,dict]`, `items: list[dict]`. Ogni gruppo aggrega: `recall@k` (su applicabili), `correttezza` (su applicabili), `nonso_lazy` (positivi finiti in nonso), `nonso_right` (negativi in nonso), `routing` (match su applicabili), `n`.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_runner.py
from src.eval.truth_set import EvalItem
from src.eval.runner import to_run_output, run_eval

def _item(**kw):
    base = dict(id="i", query="q", lang="it", branch="narrative", role="tecnico",
                doc="d", page=5, expected="foo", kind="positive",
                must_say_nonso=False, source="crop")
    base.update(kw); return EvalItem(**base)

def test_to_run_output_extracts_tuples():
    class E:  # finto Evidence
        def __init__(s,d,p): s.doc=d; s.page=p
    state = {"evidence":[E("d",5)], "visual_pages":[], "citations":[("d",5)],
             "final_level":"ALTA", "final_answer":"foo", "route":"natural","intent":"NARRATIVE"}
    out = to_run_output(state)
    assert out.evidence == [("d",5)] and out.level=="ALTA" and out.answer=="foo"

def test_run_eval_aggregates_per_lang_and_branch():
    items = [_item(id="a", lang="it"), _item(id="b", lang="en", branch="structured",
             doc="d", page=5, expected="foo")]
    def answer_fn(q, role, deps):
        return {"evidence":[type("E",(),{"doc":"d","page":5})()], "visual_pages":[],
                "citations":[("d",5)], "final_level":"ALTA","final_answer":"foo",
                "route":"code","intent":"NARRATIVE"}
    rep = run_eval(items, answer_fn, deps_for_role=lambda r: None, k=10)
    assert rep.per_lang["it"]["n"] == 1
    assert rep.per_branch["narrative"]["recall@k"] == 1.0
    assert rep.per_lang["it"]["correttezza"] == 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/pytest tests/eval/test_runner.py -v`
Expected: FAIL (module non esiste).

- [ ] **Step 3: Write minimal implementation**

```python
# src/eval/runner.py
from __future__ import annotations
from dataclasses import dataclass, field
from src.eval.metrics import (RunOutput, recall_at_k, correctness,
                              nonso_status, routing_match)

def to_run_output(state: dict) -> RunOutput:
    ev = [(e.doc, e.page) for e in state.get("evidence", []) or []]
    vp = [(p.doc, p.page) for p in state.get("visual_pages", []) or []]
    return RunOutput(
        level=state.get("final_level") or state.get("level") or "",
        answer=state.get("final_answer") or state.get("answer"),
        citations=list(state.get("citations", []) or []),
        evidence=ev, visual_pages=vp,
        route=state.get("route"), intent=state.get("intent"),
    )

@dataclass
class EvalReport:
    per_lang: dict = field(default_factory=dict)
    per_branch: dict = field(default_factory=dict)
    items: list = field(default_factory=list)

def _mean(vals: list[bool]) -> float | None:
    xs = [v for v in vals if v is not None]
    return round(sum(1 for v in xs if v) / len(xs), 3) if xs else None

def _aggregate(rows: list[dict]) -> dict:
    return {
        "n": len(rows),
        "recall@k": _mean([r["recall"] for r in rows]),
        "correttezza": _mean([r["correct"] for r in rows]),
        "routing": _mean([r["routing"] for r in rows]),
        "nonso_lazy": sum(1 for r in rows if r["kind"]=="positive" and r["nonso"]=="nonso"),
        "nonso_right": sum(1 for r in rows if r["kind"]=="negative" and r["nonso"]=="nonso"),
    }

def run_eval(items, answer_fn, deps_for_role, k: int = 10) -> EvalReport:
    rows = []
    for it in items:
        state = answer_fn(it.query, it.role, deps_for_role(it.role))
        out = to_run_output(state)
        rows.append({
            "id": it.id, "lang": it.lang, "branch": it.branch, "kind": it.kind,
            "recall": recall_at_k(it, out, k), "correct": correctness(it, out),
            "nonso": nonso_status(out), "routing": routing_match(it, out),
            "level": out.level, "answer": (out.answer or "")[:120],
        })
    rep = EvalReport(items=rows)
    for key in {r["lang"] for r in rows}:
        rep.per_lang[key] = _aggregate([r for r in rows if r["lang"]==key])
    for key in {r["branch"] for r in rows}:
        rep.per_branch[key] = _aggregate([r for r in rows if r["branch"]==key])
    return rep
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/pytest tests/eval/test_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eval/runner.py tests/eval/test_runner.py
git commit -m "eval(B): runner + aggregazione per lingua/ramo"
```

---

### Task 4: Check auto-derivati e coverage table

**Files:**
- Create: `src/eval/auto_checks.py`
- Test: `tests/eval/test_auto_checks.py`

**Interfaces:**
- Consumes: `EvalItem` (Task 1).
- Produces:
  - `derive_structural_checks(fetch_codes, per_doc:int=3) -> list[EvalItem]` — `fetch_codes() -> list[tuple[doc,page,code,prezzo]]` (iniettabile per i test). Genera item `branch="structured", kind="positive", source="auto"`, `query=f"codice {code}"`, `expected=str(prezzo)` se prezzo non-None, `doc`,`page` attesi. Max `per_doc` codici per doc.
  - `coverage_row(doc_stem, structured_docs, narrative_docs, visual_docs) -> dict` — `{"doc":..,"structured":bool,"narrative":bool,"visual":bool}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_auto_checks.py
from src.eval.auto_checks import derive_structural_checks, coverage_row

def test_derive_limits_per_doc():
    def fetch():
        return [("d1",2,"A1",78.83),("d1",3,"A2",10.0),("d1",4,"A3",5.0),
                ("d2",1,"B1",None)]
    items = derive_structural_checks(fetch, per_doc=2)
    d1 = [i for i in items if i.doc=="d1"]
    assert len(d1) == 2
    assert all(i.source=="auto" and i.branch=="structured" for i in items)
    b1 = [i for i in items if i.doc=="d2"][0]
    assert b1.expected is None and b1.query == "codice B1"

def test_coverage_row():
    row = coverage_row("d1", {"d1"}, set(), {"d1"})
    assert row == {"doc":"d1","structured":True,"narrative":False,"visual":True}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/pytest tests/eval/test_auto_checks.py -v`
Expected: FAIL (module non esiste).

- [ ] **Step 3: Write minimal implementation**

```python
# src/eval/auto_checks.py
from __future__ import annotations
from collections import defaultdict
from src.eval.truth_set import EvalItem

def derive_structural_checks(fetch_codes, per_doc: int = 3) -> list[EvalItem]:
    seen: dict[str, int] = defaultdict(int)
    out: list[EvalItem] = []
    for doc, page, code, prezzo in fetch_codes():
        if seen[doc] >= per_doc:
            continue
        seen[doc] += 1
        out.append(EvalItem(
            id=f"AUTO-{doc}-{code}", query=f"codice {code}", lang="it",
            branch="structured", role="tecnico", doc=doc, page=page,
            expected=(str(prezzo) if prezzo is not None else None),
            kind="positive", must_say_nonso=False, source="auto",
        ))
    return out

def coverage_row(doc_stem, structured_docs, narrative_docs, visual_docs) -> dict:
    return {
        "doc": doc_stem,
        "structured": doc_stem in structured_docs,
        "narrative": doc_stem in narrative_docs,
        "visual": doc_stem in visual_docs,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/pytest tests/eval/test_auto_checks.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/eval/auto_checks.py tests/eval/test_auto_checks.py
git commit -m "eval(B): check auto-derivati (plumbing) + coverage row"
```

---

### Task 5: CLI `eval_ampio.py` — wiring reale, report, coverage

**Files:**
- Create: `scripts/eval_ampio.py`
- Test: `tests/eval/test_eval_cli_smoke.py` (smoke: import + report formatter su dati finti, NO modello)

**Interfaces:**
- Consumes: `load_truth_set` (T1), `run_eval`/`EvalReport` (T3), `derive_structural_checks`/`coverage_row` (T4).
- Produces:
  - `format_report(rep: EvalReport, coverage: list[dict]) -> str` — testo leggibile: tabella per lingua, per ramo, riga copertura (quanti PDF indicizzati per ramo).
  - `main()` — carica il truth set (`output/eval/goldenset_v2/truth.json`, fallback `sample_truth.json`), costruisce `deps_for_role` via `get_deps`, esegue `run_eval` con `answer` reale, scrive il report in `output/eval/report_<timestamp>.txt` e lo stampa. Coverage via query psql (structured=`estrazione.righe_listino`, narrative/visual = Qdrant scroll doc distinti) e `find corpus -name *.pdf`.

- [ ] **Step 1: Write the failing test**

```python
# tests/eval/test_eval_cli_smoke.py
from src.eval.runner import EvalReport
from scripts.eval_ampio import format_report

def test_format_report_has_lang_and_branch():
    rep = EvalReport(
        per_lang={"it":{"n":2,"recall@k":0.5,"correttezza":1.0,"routing":1.0,"nonso_lazy":1,"nonso_right":0}},
        per_branch={"structured":{"n":2,"recall@k":0.5,"correttezza":1.0,"routing":1.0,"nonso_lazy":1,"nonso_right":0}},
        items=[])
    txt = format_report(rep, coverage=[{"doc":"d","structured":True,"narrative":False,"visual":False}])
    assert "it" in txt and "structured" in txt and "recall@k" in txt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/pytest tests/eval/test_eval_cli_smoke.py -v`
Expected: FAIL (module `scripts.eval_ampio` non esiste).

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/eval_ampio.py
from __future__ import annotations
import os, sys, json, glob
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.eval.truth_set import load_truth_set
from src.eval.runner import run_eval, EvalReport
from src.eval.auto_checks import coverage_row

def format_report(rep: EvalReport, coverage: list[dict]) -> str:
    lines = ["=== EVAL AMPIO — per LINGUA ==="]
    for lang, d in sorted(rep.per_lang.items()):
        lines.append(f"[{lang}] {d}")
    lines.append("=== per RAMO ===")
    for br, d in sorted(rep.per_branch.items()):
        lines.append(f"[{br}] {d}")
    tot = len(coverage)
    for ramo in ("structured", "narrative", "visual"):
        n = sum(1 for r in coverage if r[ramo])
        lines.append(f"COVERAGE {ramo}: {n}/{tot} PDF")
    return "\n".join(lines)

def _coverage(conn, qdrant, corpus_dir="corpus") -> list[dict]:
    pdfs = {Path(p).stem for p in glob.glob(f"{corpus_dir}/**/*.pdf", recursive=True)}
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT doc_stem FROM estrazione.righe_listino")
        structured = {r[0] for r in cur.fetchall()}
    narrative = _qdrant_docs(qdrant, "aike_chunks")
    visual = _qdrant_docs(qdrant, "aike_pages_visual")
    return [coverage_row(s, structured, narrative, visual) for s in sorted(pdfs)]

def _qdrant_docs(qdrant, collection: str) -> set[str]:
    docs, offset = set(), None
    while True:
        pts, offset = qdrant.scroll(collection_name=collection, with_payload=["doc","doc_stem"],
                                    limit=1000, offset=offset)
        for p in pts:
            docs.add(p.payload.get("doc") or p.payload.get("doc_stem"))
        if offset is None:
            break
    return {d for d in docs if d}

def main() -> int:
    import psycopg
    from qdrant_client import QdrantClient
    from src.backend.config import settings
    from src.serving.session_deps import get_deps
    from src.graph.pipeline import answer

    tp = "output/eval/goldenset_v2/truth.json"
    if not os.path.exists(tp):
        tp = "output/eval/goldenset_v2/sample_truth.json"
    items = load_truth_set(tp)

    deps_cache: dict = {}
    def deps_for_role(role):
        if role not in deps_cache:
            deps_cache[role] = get_deps(role)
        return deps_cache[role]

    rep = run_eval(items, answer, deps_for_role, k=10)

    conn = psycopg.connect(f"postgresql://{settings.postgres_user}:{settings.postgres_password}"
                           f"@{settings.postgres_host}:{settings.postgres_port}/{settings.postgres_db}")
    qdrant = QdrantClient(url=f"http://{settings.qdrant_host}:{settings.qdrant_port}",
                          api_key=settings.qdrant_api_key)
    coverage = _coverage(conn, qdrant)

    txt = format_report(rep, coverage)
    out_dir = Path("output/eval"); out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    (out_dir / f"report_{stamp}.txt").write_text(txt)
    (out_dir / f"detail_{stamp}.json").write_text(json.dumps(rep.items, indent=2, default=str))
    print(txt)
    return 0

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/pytest tests/eval/test_eval_cli_smoke.py -v`
Expected: PASS.

- [ ] **Step 5: Esecuzione reale (smoke live) + STOP per reviewer**

Run (RAW obbligatorio, il reviewer riverifica):
`env -u PYTHONPATH OLLAMA_HOST=http://localhost:11435 PYTHONPATH=. .venv/bin/python scripts/eval_ampio.py`
Expected: stampa il report per-lingua/per-ramo + COVERAGE su `sample_truth.json`; scrive `output/eval/report_*.txt`. NON deve toccare la produzione.

- [ ] **Step 6: Verifica gate invariato + commit**

Run: `env -u PYTHONPATH OLLAMA_HOST=http://localhost:11435 PYTHONPATH=. .venv/bin/python scripts/golden_gate.py` → deve restare exit 0.

```bash
git add scripts/eval_ampio.py tests/eval/test_eval_cli_smoke.py
git commit -m "eval(B): CLI eval_ampio (wiring answer reale, report per lingua/ramo, coverage)"
```

---

## Self-Review

**1. Spec coverage:**
- §3.1 set di verità → Task 1 (schema/loader; il contenuto adjudicato lo semina il reviewer nel `truth.json`).
- §3.2 check auto + coverage → Task 4 + Task 5 (`_coverage`).
- §3.3 runner+report (recall@k/correttezza/non-so/routing per lingua/ramo) → Task 2+3+5.
- §4 metriche separate → Task 2 (recall@k ≠ correttezza, funzioni distinte).
- §7 DoD: truth.json ≥30 item = **lavoro reviewer** post-piano (fuori dai task esecutore); il runner li consuma. Gate verde = Task 5 Step 6. Test rosso-prima = ogni task.
- §8 fattibilità recall@k → RISOLTA: lo stato espone `evidence`/`visual_pages` (Task 3 `to_run_output`).

**2. Placeholder scan:** nessun TBD/TODO; ogni step ha codice reale.

**3. Type consistency:** `EvalItem`/`RunOutput`/`EvalReport` usati coerenti tra i task; `derive_structural_checks(fetch_codes,...)` iniettabile (test DB-free); `to_run_output` mappa le chiavi reali di `GraphState`.

**Nota reviewer:** il set di verità reale (≥30 item adjudicati coi crop) è compito del reviewer e completa la DoD della Fase 1 dopo che il runner è verde; non è un task dell'esecutore.
