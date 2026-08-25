"""Registrazione PASSIVA degli score del ramo visivo, per poter TARARE la soglia.

Perche' esiste questo modulo
----------------------------
`VISUAL_SCORE_FLOOR = 0.20` (`src/graph/tools/visual_tool.py`) e' stata scelta a
intuito su 19 query, e nessuno sa come si distribuiscono i punteggi dei match
GIUSTI contro quelli SBAGLIATI — perche' quel numero **non viene mai registrato**.
Quando la soglia scarta, `visual_node` ritorna `visual_pages: []` e lo score che
ha causato lo scarto sparisce: resta solo una riga di audit a testo libero, che
non si aggrega. Senza quella distribuzione, qualunque nuova soglia sarebbe un
altro intuito.

Cosa NON fa
-----------
Non decide nulla. Non e' un campo di `GraphState`, non arriva all'utente finale,
non cambia una virgola di cio' che il grafo risponde. E' **spento di default**:
acceso, aggiunge una lettura di `os.getenv` e un record in memoria; spento, e' un
`return` immediato. Qualunque errore del sink viene inghiottito: un osservatore
che rompe la risposta non e' un osservatore, e' un bug.

Interruttori (convenzione `AIKE_*` del progetto)
------------------------------------------------
- `AIKE_VISUAL_TRACE=1|true`   -> registrazione accesa (default: SPENTA)
- `AIKE_VISUAL_TRACE_PATH=...` -> in aggiunta al buffer, una riga JSON per query
  appesa a questo file. Da solo NON accende nulla.

Il consumatore e' `scripts/audit_visual_graph_eval.py`, che dopo ogni query
svuota il buffer con `take()` e ne scrive una riga nel file per-query.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

TRACE_ENV = "AIKE_VISUAL_TRACE"
TRACE_PATH_ENV = "AIKE_VISUAL_TRACE_PATH"

# Esito del confronto con la soglia. Sono i tre stati distinguibili, e vanno
# tenuti distinti: "nessuna pagina" non e' "scartata dalla soglia" (nel primo
# caso la soglia non e' mai stata interrogata, e contarli insieme falserebbe
# esattamente la curva che questo modulo esiste per produrre).
OUTCOME_PASSED = "passata"
OUTCOME_REJECTED = "scartata_dalla_soglia"
OUTCOME_NO_PAGES = "nessuna_pagina"

# Il buffer vive in un processo lungo (Chainlit): bounded, altrimenti una
# sessione di un giorno diventa una perdita di memoria.
MAX_RECORDS = 500

_LOCK = threading.Lock()
_BUFFER: list["VisualScoreRecord"] = []


@dataclass(frozen=True)
class VisualScoreRecord:
    """Una query visiva, fotografata PRIMA che la soglia decida.

    `top_score` e' il punteggio della pagina migliore **prima** del confronto con
    `score_floor`: e' il numero che oggi si perde. `pages` porta le prime K
    pagine con `(doc, page, score)` di ciascuna, perche' una soglia si tara
    guardando la separazione fra il primo giusto e il primo sbagliato, non il
    solo massimo.
    """

    query: str
    role: str | None
    pages: tuple[tuple[str, int, float], ...]
    top_score: float | None
    score_floor: float | None
    outcome: str
    # `score_floor is not None` significa "il gate era armato". In produzione
    # e' vero SOLO col reranker visivo attivo (`src/serving/session_deps.py`:
    # `floor = VISUAL_SCORE_FLOOR if _visual_reranker is not None else None`),
    # perche' la soglia e' tarata sulla scala di score del reranker.
    floor_gate_armed: bool
    # Osservato, non previsto: `None` = non osservabile (il run non passa da
    # `session_deps`, tipicamente un test o un binder fatto a mano).
    reranker_active: bool | None

    def as_dict(self) -> dict:
        return {
            "query": self.query,
            "role": self.role,
            "pages": [{"doc": d, "page": p, "score": s} for d, p, s in self.pages],
            "top_score": self.top_score,
            "score_floor": self.score_floor,
            "outcome": self.outcome,
            "floor_gate_armed": self.floor_gate_armed,
            "reranker_active": self.reranker_active,
        }


def trace_enabled() -> bool:
    """Interruttore unico. Spento di default, e spento su qualunque valore ignoto."""
    return os.getenv(TRACE_ENV, "0").strip().lower() in ("1", "true")


def _observed_reranker() -> bool | None:
    """Il reranker visivo era davvero istanziato in questo processo?

    Si LEGGE il modulo se e' gia' importato, non lo si importa: l'osservatore non
    deve tirarsi dietro `session_deps` (ne' il suo costo, ne' il rischio di
    import circolare con `pipeline`). Modulo assente -> `None` = non osservabile.
    """
    mod = sys.modules.get("src.serving.session_deps")
    if mod is None:
        return None
    return getattr(mod, "_visual_reranker", None) is not None


def _append_jsonl(path: str, payload: dict) -> None:
    dest = Path(path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def record(*, query: str, role: str | None, pages: Sequence, floor: float | None,
           outcome: str) -> None:
    """Registra una query visiva. NO-OP quando la registrazione e' spenta.

    `pages` sono i `PageHit` **come tornano dal retrieval**, prima di qualunque
    filtro della soglia. Nessuna eccezione esce da qui.
    """
    if not trace_enabled():
        return
    try:
        righe = tuple((p.doc, int(p.page), float(p.score)) for p in pages)
        rec = VisualScoreRecord(
            query=query,
            role=role,
            pages=righe,
            top_score=righe[0][2] if righe else None,
            score_floor=floor,
            outcome=outcome,
            floor_gate_armed=floor is not None,
            reranker_active=_observed_reranker(),
        )
        with _LOCK:
            _BUFFER.append(rec)
            if len(_BUFFER) > MAX_RECORDS:
                del _BUFFER[:-MAX_RECORDS]
        path = os.getenv(TRACE_PATH_ENV)
        if path:
            _append_jsonl(path, rec.as_dict())
    except Exception as exc:  # un osservatore non puo' rompere la risposta
        logger.warning("visual_trace: registrazione fallita (%s) -> ignorata", exc)


def take() -> list[VisualScoreRecord]:
    """Svuota e restituisce i record accumulati (il consumatore e' l'audit)."""
    with _LOCK:
        out = list(_BUFFER)
        _BUFFER.clear()
    return out


def snapshot() -> list[VisualScoreRecord]:
    """Legge senza svuotare (diagnostica interattiva)."""
    with _LOCK:
        return list(_BUFFER)


def reset() -> None:
    with _LOCK:
        _BUFFER.clear()
