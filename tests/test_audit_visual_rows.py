"""L'audit visivo deve emettere UNA RIGA PER QUERY, coi punteggi pre-soglia.

Il difetto che questi test presidiano e' di METODO, non di calcolo: finche'
l'audit dice solo "quante query passano", la soglia si puo' solo indovinare. La
riga per query esiste per una cosa sola: mettere accanto, per ogni domanda, il
punteggio del bersaglio GIUSTO e quello degli SBAGLIATI, e leggere la soglia
dalla loro separazione.

Il caso che conta e' quello in cui la soglia SCARTA: li' `visual_pages` e' vuoto,
la metrica storica vede solo un MISS, e senza il trace non resta nulla per
distinguere "il motore non ha trovato la pagina" da "il motore l'aveva in cima e
la soglia l'ha buttata". Sono due difetti opposti con due rimedi opposti.

Nessun modello: `answer` e il trace sono iniettati, e i finti APPLICANO il
comportamento (la soglia viene davvero confrontata) invece di asserire sulla
forma — la disciplina di `tests/test_delete_document_points.py`.
"""

from __future__ import annotations

import json

import pytest

from src.graph.visual_trace import (OUTCOME_NO_PAGES, OUTCOME_PASSED,
                                    OUTCOME_REJECTED, VisualScoreRecord)
from src.retrieval.pixelrag.index import PageHit
from scripts.audit_visual_graph_eval import evaluate, write_rows

FLOOR = 0.20


def _pair(qa_id, query, doc, pages, lang="IT"):
    return {"qa_id": qa_id, "query": query, "lang": lang,
            "expected": [{"doc": doc, "pages": list(pages)}]}


# ------------------------------------------------------------------ il finto

class MotoreFinto:
    """Applica DAVVERO la soglia, come `visual_node`, e registra come il trace.

    `catalogo` mappa query -> pagine ordinate (doc, page, score). Non asserisce
    nulla: e' il comportamento del ramo visivo ridotto all'osso.
    """

    def __init__(self, catalogo, floor=FLOOR, reranker=True):
        self.catalogo = catalogo
        self.floor = floor
        self.reranker = reranker
        self._trace: list[VisualScoreRecord] = []
        self.viste: list[tuple[str, str]] = []

    def answer(self, query, role, deps=None):
        self.viste.append((query, role))
        trovate = [PageHit(doc=d, page=p, score=s, n_tiles=1)
                   for d, p, s in self.catalogo.get(query, [])]
        if not trovate:
            self._registra(query, role, (), OUTCOME_NO_PAGES)
            return {"visual_pages": [], "level": "BASSA"}
        sotto = self.floor is not None and trovate[0].score < self.floor
        self._registra(query, role, trovate,
                       OUTCOME_REJECTED if sotto else OUTCOME_PASSED)
        if sotto:
            return {"visual_pages": [], "level": "BASSA"}
        return {"visual_pages": trovate, "level": "MEDIA"}

    def _registra(self, query, role, pages, outcome):
        righe = tuple((p.doc, p.page, p.score) for p in pages)
        self._trace.append(VisualScoreRecord(
            query=query, role=role, pages=righe,
            top_score=righe[0][2] if righe else None,
            score_floor=self.floor, outcome=outcome,
            floor_gate_armed=self.floor is not None,
            reranker_active=self.reranker))

    def take(self):
        out, self._trace = self._trace, []
        return out


def _esegui(pairs, catalogo, *, indexed=None, floor=FLOOR, sources=None):
    motore = MotoreFinto(catalogo, floor=floor)
    if indexed is None:
        indexed = {(c["doc"], p) for pair in pairs
                   for c in pair["expected"] for p in c["pages"]}
    coppie = list(zip(sources or ["set"] * len(pairs), pairs))
    righe = evaluate(coppie, indexed=indexed,
                     doc_roles={c["doc"]: "tecnico" for pair in pairs
                                for c in pair["expected"]},
                     answer_fn=motore.answer, deps_fn=lambda r: None,
                     trace_fn=motore.take)
    return motore, righe


# ------------------------------------------------------- la riga per query

class TestRigaPerQuery:

    def test_una_riga_per_query(self):
        pairs = [_pair("V1", "esploso pompa", "docA", [7]),
                 _pair("V2", "esploso cambio", "docB", [3])]
        _, righe = _esegui(pairs, {"esploso pompa": [("docA", 7, 0.62)],
                                   "esploso cambio": [("docB", 3, 0.55)]})
        assert [r["qa_id"] for r in righe] == ["V1", "V2"]

    def test_bersaglio_trovato_riporta_posizione_e_punteggi(self):
        pairs = [_pair("V1", "esploso pompa", "docA", [7])]
        _, (r,) = _esegui(pairs, {"esploso pompa": [("docZ", 1, 0.71),
                                                    ("docA", 7, 0.62),
                                                    ("docY", 4, 0.30)]})
        assert r["status"] == "EVALUATED"
        assert r["expected"] == [["docA", 7]]
        assert r["topk_pre_floor"] == [{"doc": "docZ", "page": 1, "score": 0.71},
                                       {"doc": "docA", "page": 7, "score": 0.62},
                                       {"doc": "docY", "page": 4, "score": 0.30}]
        assert r["target_in_topk_pre_floor"] is True
        assert r["target_rank_pre_floor"] == 2
        assert r["target_score"] == pytest.approx(0.62)
        assert r["top_score_pre_floor"] == pytest.approx(0.71)
        assert r["floor_outcome"] == OUTCOME_PASSED
        assert r["score_floor"] == FLOOR
        assert r["reranker_active"] is True
        assert r["lang"] == "IT" and r["role"] == "tecnico"

    def test_lo_scarto_della_soglia_non_perde_i_punteggi(self):
        """IL caso: il bersaglio ERA in cima, la soglia l'ha buttato via."""
        pairs = [_pair("V1", "esploso pompa", "docA", [7])]
        _, (r,) = _esegui(pairs, {"esploso pompa": [("docA", 7, 0.14),
                                                    ("docB", 2, 0.09)]})
        assert r["floor_outcome"] == OUTCOME_REJECTED
        assert r["shown"] == [], "il sistema non mostra nulla, ed e' giusto cosi'"
        assert r["rank"] is None, "la metrica storica vede un MISS..."
        assert r["target_rank_pre_floor"] == 1, "...ma il bersaglio era primo"
        assert r["target_score"] == pytest.approx(0.14)
        assert r["top_score_pre_floor"] == pytest.approx(0.14)

    def test_distingue_scarto_da_bersaglio_assente_dal_retrieval(self):
        """Due MISS identici per la metrica, due difetti opposti."""
        pairs = [_pair("V1", "q scarto", "docA", [7]),
                 _pair("V2", "q assente", "docB", [3])]
        _, (scarto, assente) = _esegui(
            pairs, {"q scarto": [("docA", 7, 0.14)],
                    "q assente": [("docZ", 9, 0.80), ("docY", 1, 0.60)]})
        assert scarto["rank"] is None and assente["rank"] is None
        assert scarto["target_rank_pre_floor"] == 1
        assert assente["target_rank_pre_floor"] is None
        assert assente["floor_outcome"] == OUTCOME_PASSED
        assert assente["target_score"] is None

    def test_zero_pagine(self):
        pairs = [_pair("V1", "q muta", "docA", [7])]
        _, (r,) = _esegui(pairs, {})
        assert r["floor_outcome"] == OUTCOME_NO_PAGES
        assert r["topk_pre_floor"] == [] and r["top_score_pre_floor"] is None

    def test_soglia_disarmata_e_dichiarata(self):
        """reranker spento -> nessun gate: la riga deve dirlo, non tacerlo."""
        pairs = [_pair("V1", "esploso pompa", "docA", [7])]
        _, (r,) = _esegui(pairs, {"esploso pompa": [("docA", 7, 0.05)]},
                          floor=None)
        assert r["score_floor"] is None
        assert r["floor_gate_armed"] is False
        assert r["floor_outcome"] == OUTCOME_PASSED
        assert r["rank"] == 1

    def test_bersaglio_non_indicizzato_non_e_valutabile(self):
        """Un bersaglio assente dall'indice e' un difetto del METRO, non del motore."""
        pairs = [_pair("V1", "esploso pompa", "docA", [7])]
        motore, righe = _esegui(pairs, {"esploso pompa": [("docA", 7, 0.62)]},
                                indexed=set())
        (r,) = righe
        assert r["status"] == "NON_VALUTABILE"
        assert r["missing_pages"] == [["docA", 7]]
        assert motore.viste == [], "non si interroga il motore su un metro rotto"

    def test_la_provenienza_del_truth_set_resta_nella_riga(self):
        """Un run solo su piu' truth set: senza `source` non sono separabili."""
        pairs = [_pair("V1", "q1", "docA", [7]), _pair("CV1", "q2", "docB", [3])]
        _, righe = _esegui(pairs, {"q1": [("docA", 7, 0.62)],
                                   "q2": [("docB", 3, 0.62)]},
                           sources=["eval_visual_v0", "eval_visual_clean"])
        assert [r["source"] for r in righe] == ["eval_visual_v0",
                                                "eval_visual_clean"]

    def test_il_trace_di_una_query_non_sporca_la_successiva(self):
        pairs = [_pair("V1", "q1", "docA", [7]), _pair("V2", "q2", "docB", [3])]
        _, (a, b) = _esegui(pairs, {"q1": [("docA", 7, 0.62)],
                                    "q2": [("docB", 3, 0.11)]})
        assert a["top_score_pre_floor"] == pytest.approx(0.62)
        assert b["top_score_pre_floor"] == pytest.approx(0.11)


class TestScrittura:

    def test_file_jsonl_una_riga_per_query(self, tmp_path):
        pairs = [_pair("V1", "q1", "docA", [7]), _pair("V2", "q2", "docB", [3])]
        _, righe = _esegui(pairs, {"q1": [("docA", 7, 0.62)],
                                   "q2": [("docB", 3, 0.11)]})
        dest = tmp_path / "sotto" / "righe.jsonl"
        write_rows(righe, dest)
        letti = [json.loads(r) for r in dest.read_text(encoding="utf-8").splitlines()]
        assert len(letti) == 2
        assert letti[0]["qa_id"] == "V1"
        assert letti[1]["floor_outcome"] == OUTCOME_REJECTED
        # la riga deve bastare a se stessa: chi la legge non ha il resto del run
        for r in letti:
            assert {"qa_id", "source", "query", "lang", "expected",
                    "topk_pre_floor", "top_score_pre_floor", "target_score",
                    "target_rank_pre_floor", "score_floor", "floor_gate_armed",
                    "floor_outcome", "rank", "shown"} <= set(r)
