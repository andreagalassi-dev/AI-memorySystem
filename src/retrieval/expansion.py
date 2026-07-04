"""Debito §2 — Query Expansion DETERMINISTICA (dizionario sinonimi a DB).

Problema (thecnical-debt-analyst.md §2): il canale BM25 da' 0 sulle query
naturali cross-lingua ("piston pin" vs "spinotto pistone") e il denso da solo
non genera margine -> rifiuto BASSA. Qui la query viene espansa coi termini
fratelli del dizionario tecnico (Postgres, seed config/synonyms.json): niente
LLM a query-time, stessa filosofia del router (deterministico, auditabile).

Matching: n-grammi della query (3->1, longest-first, span consumati) contro i
termini approvati; l'espansione sono i termini fratelli dei concetti colpiti,
esclusi quelli gia' presenti nella query, cap MAX_TERMS. I termini vanno SOLO
nel canale sparse della route 'natural' (search.py): dense e reranker restano
sulla query originale; le route 'code'/'mixed' non sono MAI espanse.

Caricamento: cache di processo + reload_synonyms() (pattern brands.py). Il
degrado e' gestito dal chiamante (nodo grafo: NON-fatale, come fusion_node):
qui si solleva RuntimeError con rimedio chiaro.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

MAX_TERMS = 8
_NGRAM_MAX = 3
_WORD = re.compile(r"[0-9a-zà-ÿäöüß*./-]+")


def _load_from_db() -> dict:
    import psycopg

    from src.backend.clients.postgres_client import get_pg_conn
    from src.registry.synonyms import load_synonyms
    try:
        with get_pg_conn() as conn:
            return load_synonyms(conn, only_approved=True)
    except psycopg.errors.UndefinedTable as e:
        raise RuntimeError(
            "Dizionario sinonimi assente: eseguire "
            "`uv run scripts/seed_synonyms.py`") from e
    except psycopg.OperationalError as e:
        raise RuntimeError(
            "Dizionario sinonimi non caricabile: Postgres irraggiungibile "
            "(docker compose up -d postgres)") from e


@lru_cache(maxsize=1)
def _index() -> dict[str, list[tuple[str, tuple[str, ...]]]]:
    """term -> lista di (slug, termini-del-concetto in ordine di seed)."""
    idx: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
    for slug, c in _load_from_db().items():
        terms = tuple(t for t, _ in c["terms"])
        for t in terms:
            idx.setdefault(t, []).append((slug, terms))
    return idx


def reload_synonyms() -> None:
    """Invalida la cache di processo (test, aggiornamenti del dizionario)."""
    _index.cache_clear()


def _tokens(text: str) -> list[str]:
    return _WORD.findall(unicodedata.normalize("NFKC", text or "").lower())


def expand_query(query: str, max_terms: int = MAX_TERMS) -> list[str]:
    """Termini di espansione per la query (deterministici, ordinati, cap).

    Longest-match-first: un n-gramma che matcha consuma i suoi token (nessun
    doppio conteggio del singolo token dentro il multi-parola).
    """
    toks = _tokens(query)
    idx = _index()
    have = set(toks)

    matched: list[tuple[str, tuple[str, ...]]] = []
    seen_slugs: set[str] = set()
    used = [False] * len(toks)
    for n in range(_NGRAM_MAX, 0, -1):
        for i in range(len(toks) - n + 1):
            if any(used[i:i + n]):
                continue
            gram = " ".join(toks[i:i + n])
            for slug, terms in idx.get(gram, []):
                if slug not in seen_slugs:
                    seen_slugs.add(slug)
                    matched.append((slug, terms))
                used[i:i + n] = [True] * n

    out: list[str] = []
    for _slug, terms in matched:
        for t in terms:
            if len(out) >= max_terms:
                return out
            # gia' in query (tutte le sue parole) o gia' emesso -> salta
            if set(t.split()) <= have or t in out:
                continue
            out.append(t)
    return out
