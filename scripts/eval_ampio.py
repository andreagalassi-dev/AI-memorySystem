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
