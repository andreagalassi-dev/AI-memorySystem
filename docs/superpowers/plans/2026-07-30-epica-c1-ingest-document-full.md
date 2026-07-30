# Epica C.1 — `ingest_document_full` (orchestratore ingestion unificata) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Un orchestratore per-documento che compone i tre entry-point di ingestion esistenti (strutturato + narrativo + visivo), fail-closed per-binario, con report dei conteggi/scarti.

**Architecture:** Due strati. (1) `ingest_document_full` = composizione PURA di tre callable `BranchFn` iniettabili: esegue ciascun binario, isola i fallimenti (uno che solleva non ferma gli altri), aggrega un `IngestReport`. Testabile senza modelli né DB (fake BranchFn). (2) `build_branches(...)` = wiring che lega i tre entry-point reali (`run_document_massive`, `ingest_one.ingest_document`, `pixelrag.ingest.ingest_document`) alle `BranchFn`, con parametri di destinazione (schema/collection) per il **sandbox**.

**Tech Stack:** Python 3.11, psycopg3, Docling (via `AIKE_TABLE_EXTRACTOR=docling`), Qdrant, PyMuPDF (fitz), pytest.

## Global Constraints

- Test/gate/eval SEMPRE con prefisso: `env -u PYTHONPATH OLLAMA_HOST=http://localhost:11435 PYTHONPATH=. .venv/bin/python -m pytest ...` — mai `python` nudo.
- **Idempotenza + additività**: upsert/delete-before-upsert nei sotto-componenti; MAI `drop` globale. L'orchestratore non aggiunge stato: delega.
- **Fail-closed per-binario**: se un binario solleva, gli altri proseguono; il fallimento va in `IngestReport.scarti[branch]`, mai silenzioso.
- **Nessun `answer()`** nell'ingestion (popolamento puro).
- **Sandbox**: i target (schema Postgres, collezioni Qdrant) sono parametri → i test/spike girano su schema/collezioni temp senza toccare produzione.
- **RBAC**: `role` di default = `tecnico` (accesso più ampio) se il profilo brand non ne impone altri; stesso meccanismo di `ingest_one` (`resolve(doc_stem)`), non inventato.
- Firme esatte degli entry-point (verificate coi tool, 30/07):
  - strutturato: `src/ingestion/run_massive_extraction.py::run_document_massive(filepath, conn, extractor, multiplexer, model_name, schema=DEFAULT_SCHEMA, dpi=200, max_pages=0, ocr=None, text_extractor_factory=None) -> MassiveDocumentOutcome`
  - narrativo: `src/ingestion/ingest_one.py::ingest_document(filepath, *, doc_stem, role, conn, is_update=False, cat="unknown", collection=None, extract_fn=None, extract_limit=0) -> dict` (chiavi: `n_chunks`, `n_points`)
  - visivo: `src/retrieval/pixelrag/ingest.py::ingest_document(doc, *, doc_stem, roles: list[Role], embedder, ...) -> IngestOutcome` (attributi: `n_pages`, `visual_pages`)

---

### Task 1: `IngestReport` + `ingest_document_full` (composizione pura, fail-closed)

**Files:**
- Create: `src/ingestion/ingest_full.py`
- Test: `tests/test_ingest_full.py`

**Interfaces:**
- Consumes: nulla (strato puro).
- Produces:
  - `IngestReport` dataclass: `doc_stem: str`, `structured_rows: int=0`, `narrative_chunks: int=0`, `visual_pages: int=0`, `scarti: dict[str,str]`, `timings: dict[str,float]`.
  - `BranchFn = Callable[[], int]` (esegue un binario, ritorna il conteggio, solleva su errore).
  - `ingest_document_full(doc_stem: str, *, structured: BranchFn, narrative: BranchFn, visual: BranchFn) -> IngestReport`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ingest_full.py
from src.ingestion.ingest_full import ingest_document_full, IngestReport

def _ok(n):        # fabbrica una BranchFn che ritorna n
    return lambda: n
def _boom(msg):    # fabbrica una BranchFn che solleva
    def f(): raise RuntimeError(msg)
    return f

def test_tutti_i_binari_ok_aggrega_i_conteggi():
    r = ingest_document_full("mamoli", structured=_ok(151), narrative=_ok(40), visual=_ok(18))
    assert (r.doc_stem, r.structured_rows, r.narrative_chunks, r.visual_pages) == ("mamoli", 151, 40, 18)
    assert r.scarti == {}
    assert set(r.timings) == {"structured", "narrative", "visual"}

def test_un_binario_fallito_non_ferma_gli_altri():
    r = ingest_document_full("nsk", structured=_boom("docling giu'"), narrative=_ok(30), visual=_ok(12))
    assert r.structured_rows == 0            # fallito -> resta 0
    assert r.narrative_chunks == 30 and r.visual_pages == 12   # gli altri proseguono
    assert "structured" in r.scarti and "docling giu'" in r.scarti["structured"]
    assert "narrative" not in r.scarti and "visual" not in r.scarti

def test_report_e_una_dataclass_serializzabile():
    r = ingest_document_full("x", structured=_ok(1), narrative=_ok(1), visual=_ok(1))
    assert isinstance(r, IngestReport)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/python -m pytest tests/test_ingest_full.py -v`
Expected: FAIL con `ModuleNotFoundError: src.ingestion.ingest_full`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/ingestion/ingest_full.py
"""Epica C.1 — Orchestratore ingestion unificata (composizione pura, fail-closed).

Compone i tre binari (strutturato/narrativo/visivo) per UN documento. Non aggiunge
stato: delega ai sotto-componenti (idempotenti/additivi). Un binario che solleva NON
ferma gli altri: il fallimento finisce in IngestReport.scarti (mai silenzioso)."""
from __future__ import annotations
import time
from collections.abc import Callable
from dataclasses import dataclass, field

BranchFn = Callable[[], int]

@dataclass
class IngestReport:
    doc_stem: str
    structured_rows: int = 0
    narrative_chunks: int = 0
    visual_pages: int = 0
    scarti: dict[str, str] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)

def ingest_document_full(doc_stem: str, *, structured: BranchFn,
                         narrative: BranchFn, visual: BranchFn) -> IngestReport:
    report = IngestReport(doc_stem=doc_stem)
    for name, fn, attr in (("structured", structured, "structured_rows"),
                           ("narrative", narrative, "narrative_chunks"),
                           ("visual", visual, "visual_pages")):
        t0 = time.perf_counter()
        try:
            setattr(report, attr, int(fn()))
        except Exception as exc:  # fail-closed per-binario: isola, non propaga
            report.scarti[name] = f"{type(exc).__name__}: {exc}"
        report.timings[name] = round(time.perf_counter() - t0, 3)
    return report
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `env -u PYTHONPATH PYTHONPATH=. .venv/bin/python -m pytest tests/test_ingest_full.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add src/ingestion/ingest_full.py tests/test_ingest_full.py
git commit -m "feat(epica C.1): ingest_document_full — composizione pura fail-closed dei 3 binari"
```

---

### Task 2: Wiring reale `build_branches` + smoke sandbox + idempotenza

**Files:**
- Modify: `src/ingestion/ingest_full.py` (aggiunge `build_branches`)
- Test: `tests/test_ingest_full_wiring.py`

**Interfaces:**
- Consumes: `ingest_document_full`, `BranchFn` (Task 1); i tre entry-point reali (firme in Global Constraints).
- Produces:
  - `build_branches(pdf_path: str, doc_stem: str, role: str, *, conn, multiplexer, model_name: str, embedder, schema: str = "estrazione", chunks_collection: str | None = None, visual_collection: str = "aike_pages_visual", max_pages: int = 0) -> dict[str, BranchFn]` — ritorna `{"structured":fn, "narrative":fn, "visual":fn}` che chiudono sui tre entry-point reali con i target dati (schema/collezioni → sandbox). Ogni `BranchFn` estrae il conteggio dal risultato: strutturato = `MassiveDocumentOutcome` righe totali; narrativo = `dict["n_points"]`; visivo = `IngestOutcome.visual_pages`.

- [ ] **Step 1: Write the failing test (wiring con doppio giro = idempotenza)**

```python
# tests/test_ingest_full_wiring.py
# Smoke LIVE su 1 doc PICCOLO in SANDBOX (schema+collezioni temp). Richiede
# Postgres+Qdrant attivi e il modello via 11435. Marcato 'live' per poterlo
# selezionare/deselezionare.
import os, uuid, pytest, psycopg
from src.ingestion.ingest_full import build_branches, ingest_document_full

pytestmark = pytest.mark.live

SMALL_PDF = "corpus/original_dataset/industeel_steel_surcharges_2026.pdf"  # 1 pagina, listino

@pytest.mark.skipif(not os.path.exists(SMALL_PDF), reason="pdf campione assente")
def test_wiring_sandbox_idempotente():
    # ... apre conn, multiplexer, embedder reali (vedi helper get_deps ingestion);
    # schema temp + collezioni temp con suffisso uuid per isolamento totale.
    sfx = uuid.uuid4().hex[:8]
    schema = f"c_spike_{sfx}"; chunks_c = f"chunks_{sfx}"; visual_c = f"visual_{sfx}"
    # (setup risorse reali qui; teardown DROP schema/collezioni nel finally)
    branches1 = build_branches(SMALL_PDF, "industeel_steel_surcharges_2026", "tecnico",
                               conn=conn, multiplexer=mux, model_name=MODEL, embedder=emb,
                               schema=schema, chunks_collection=chunks_c, visual_collection=visual_c)
    r1 = ingest_document_full("industeel_steel_surcharges_2026", **branches1)
    assert r1.structured_rows > 0 or r1.narrative_chunks > 0   # il listino produce dati
    assert r1.scarti == {}                                     # nessun binario fallito
    # secondo giro identico -> idempotenza: conteggi stabili, nessun duplicato
    branches2 = build_branches(SMALL_PDF, "industeel_steel_surcharges_2026", "tecnico",
                               conn=conn, multiplexer=mux, model_name=MODEL, embedder=emb,
                               schema=schema, chunks_collection=chunks_c, visual_collection=visual_c)
    r2 = ingest_document_full("industeel_steel_surcharges_2026", **branches2)
    assert r2.structured_rows == r1.structured_rows
    assert r2.narrative_chunks == r1.narrative_chunks
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -u PYTHONPATH OLLAMA_HOST=http://localhost:11435 PYTHONPATH=. .venv/bin/python -m pytest tests/test_ingest_full_wiring.py -v`
Expected: FAIL con `ImportError: cannot import name 'build_branches'`.

- [ ] **Step 3: Implement `build_branches`**

Legare i tre entry-point (import con alias per evitare la collisione di nome `ingest_document`):

```python
# in src/ingestion/ingest_full.py
def build_branches(pdf_path, doc_stem, role, *, conn, multiplexer, model_name,
                   embedder, schema="estrazione", chunks_collection=None,
                   visual_collection="aike_pages_visual", max_pages=0):
    import fitz
    from src.ingestion.run_massive_extraction import run_document_massive, get_text_extractor_factory
    from src.ingestion.ingest_one import ingest_document as _narrative_ingest
    from src.retrieval.pixelrag.ingest import ingest_document as _visual_ingest
    from src.registry.roles import Role  # adattare all'import reale del tipo Role

    os_env = __import__("os").environ
    os_env["AIKE_TABLE_EXTRACTOR"] = "docling"   # Docling = estrattore scelto (arco D-B)

    def structured() -> int:
        out = run_document_massive(pdf_path, conn, extractor=None_or_heavy, multiplexer=multiplexer,
                                   model_name=model_name, schema=schema, max_pages=max_pages,
                                   text_extractor_factory=get_text_extractor_factory())
        return out.righe_totali  # adattare al nome reale del conteggio in MassiveDocumentOutcome

    def narrative() -> int:
        res = _narrative_ingest(pdf_path, doc_stem=doc_stem, role=role, conn=conn,
                                is_update=True, collection=chunks_collection, extract_limit=max_pages)
        return int(res["n_points"])

    def visual() -> int:
        doc = fitz.open(pdf_path)
        try:
            out = _visual_ingest(doc, doc_stem=doc_stem, roles=[Role(role)], embedder=embedder,
                                 collection=visual_collection)  # confermare il param collection
            return int(out.visual_pages)
        finally:
            doc.close()

    return {"structured": structured, "narrative": narrative, "visual": visual}
```

NOTE per l'esecutore (da confermare coi tool prima di scrivere, NON indovinare):
1. nome reale del conteggio-righe in `MassiveDocumentOutcome` (grep la dataclass);
2. l'entry visivo accetta `collection=`? Se no, aggiungere il parametro all'entry o usare il suo meccanismo di collezione (mai droppare la reale);
3. `extractor` HEAVY per `run_document_massive`: per i born-digital il `text_extractor_factory` Docling copre le tabelle; per gli scan puri serve `ocr=`/extractor VLM (rimandato a C.5). Per lo smoke born-digital, passare un extractor HEAVY inerte o quello di default del runner.

- [ ] **Step 4: Run the live smoke to verify it passes**

Run: `env -u PYTHONPATH OLLAMA_HOST=http://localhost:11435 PYTHONPATH=. .venv/bin/python -m pytest tests/test_ingest_full_wiring.py -v -m live`
Expected: PASS; teardown droppa schema/collezioni temp. **Leggi il log**, non solo l'exit code.

- [ ] **Step 5: Verifica non-regressione + Commit**

Run: `env -u PYTHONPATH OLLAMA_HOST=http://localhost:11435 PYTHONPATH=. .venv/bin/python -m pytest -q -m "not live"`
Expected: 813+ passed (nessuna regressione dai binari esistenti).

```bash
git add src/ingestion/ingest_full.py tests/test_ingest_full_wiring.py
git commit -m "feat(epica C.1): build_branches — wiring reale dei 3 binari + smoke sandbox idempotente"
```

---

## Self-Review (reviewer)

- **Copertura spec §4/§9**: Task 1 = orchestratore puro + IngestReport (contratto §9); Task 2 = wiring dei 3 entry-point con target sandbox (§5) + idempotenza (§4). RBAC via `role` param (default tecnico) come da Global Constraints. ✓
- **Fuori da C.1 (per i punti successivi)**: C.2 spike su 5 doc; C.3 giro sui 62 con snapshot+OK; C.4 parziali; C.5 OCR. Non in questo piano.
- **Placeholder**: le NOTE al Task 2 Step 3 sono punti da CONFERMARE coi tool (nome conteggio, param collection visivo) — l'esecutore li verifica prima di scrivere, non li indovina. Il resto è codice reale.
- **Rischio**: nessuna mutazione di produzione in C.1 (solo sandbox temp con teardown). La produzione si tocca da C.3 con snapshot+OK.
