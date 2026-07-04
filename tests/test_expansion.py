"""Debito §2 — Test della Query Expansion (dizionario, matcher, nodo grafo).

Vincoli sotto test: espansione DETERMINISTICA e cappata; SOLO route natural;
nodo NON-fatale (dizionario giu' -> comportamento senza espansione, mai
blocco); la query in stato non viene mai riscritta; round-trip seed<->DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.retrieval import expansion
from src.retrieval.expansion import expand_query, reload_synonyms
from tests.conftest import needs_pg

SEED_PATH = Path(__file__).resolve().parents[1] / "config" / "synonyms.json"

FAKE_DICT = {
    "spinotto": {"note": None, "terms": [["spinotto", "IT"],
                                         ["piston pin", "EN"],
                                         ["kolbenbolzen", "DE"]]},
    "spia-livello": {"note": None, "terms": [["spia", "IT"], ["vetrino", "IT"],
                                             ["livello visivo", "IT"],
                                             ["sight glass", "EN"]]},
    "vite": {"note": None, "terms": [["vite", "IT"], ["screw", "EN"],
                                     ["schraube", "DE"]]},
}


@pytest.fixture()
def fake_dict(monkeypatch):
    monkeypatch.setattr(expansion, "_load_from_db", lambda: FAKE_DICT)
    reload_synonyms()
    yield
    reload_synonyms()


class TestMatcher:
    def test_espansione_cross_lingua(self, fake_dict):
        assert expand_query("spinotto del pistone Rotax") == [
            "piston pin", "kolbenbolzen"]

    def test_multi_parola_longest_first(self, fake_dict):
        # 'livello visivo' matcha come bigramma: niente doppio match sul singolo
        out = expand_query("livello visivo per SCV")
        assert out == ["spia", "vetrino", "sight glass"]

    def test_termini_gia_in_query_esclusi(self, fake_dict):
        out = expand_query("vite screw")
        assert "vite" not in out and "screw" not in out
        assert out == ["schraube"]

    def test_cap_max_terms(self, fake_dict):
        out = expand_query("vite spinotto spia", max_terms=3)
        assert len(out) == 3

    def test_deterministica(self, fake_dict):
        q = "spinotto e vite della spia"
        assert expand_query(q) == expand_query(q)

    def test_query_senza_match(self, fake_dict):
        assert expand_query("V09156800007") == []
        assert expand_query("") == []

    def test_case_e_accenti_normalizzati(self, fake_dict):
        assert expand_query("SPINOTTO") == ["piston pin", "kolbenbolzen"]


class TestNodoGrafo:
    def _mk_deps(self, expand=None, retrieve=None):
        from src.graph.pipeline import Deps
        from src.retrieval.confidence import CALIBRATED_V1
        return Deps(retrieve=retrieve or (lambda q, t=None: []),
                    synthesize=lambda q, ev: "", catalog_lookup=lambda k: False,
                    thresholds=CALIBRATED_V1, expand=expand)

    def test_solo_route_natural(self):
        from src.graph.pipeline import expansion_node
        deps = self._mk_deps(expand=lambda q: ["boccola"])
        assert expansion_node({"route": "code", "query": "x"}, deps) == {}
        assert expansion_node({"route": "mixed", "query": "x"}, deps) == {}
        out = expansion_node({"route": "natural", "query": "x"}, deps)
        assert out["expansion_terms"] == ["boccola"]

    def test_senza_expand_dep_no_op(self):
        from src.graph.pipeline import expansion_node
        assert expansion_node({"route": "natural", "query": "x"},
                              self._mk_deps(expand=None)) == {}

    def test_non_fatale_su_errore(self):
        from src.graph.pipeline import expansion_node

        def boom(q):
            raise RuntimeError("Postgres irraggiungibile")
        out = expansion_node({"route": "natural", "query": "x"},
                             self._mk_deps(expand=boom))
        assert out["expansion_terms"] == []
        assert "NON-FATALE" in out["audit"][0]

    def test_termini_fluiscono_a_retrieve_query_intatta(self):
        from src.graph.pipeline import build_graph
        seen = {}

        def spy_retrieve(query, terms=None):
            seen["query"], seen["terms"] = query, terms
            return []

        deps = self._mk_deps(expand=lambda q: ["bushing", "gleitlager"],
                             retrieve=spy_retrieve)
        g = build_graph(deps)
        g.invoke({"query": "boccola del trinciaerba", "role": "tecnico"})
        assert seen["query"] == "boccola del trinciaerba"   # MAI riscritta
        assert seen["terms"] == ["bushing", "gleitlager"]

    def test_retrocompatibile_retrieve_un_argomento(self):
        # senza expand (o senza termini) retrieve e' chiamata con la sola query
        from src.graph.pipeline import build_graph
        seen = {}

        def legacy_retrieve(query):
            seen["query"] = query
            return []

        deps = self._mk_deps(expand=None, retrieve=legacy_retrieve)
        build_graph(deps).invoke({"query": "boccola", "role": "tecnico"})
        assert seen["query"] == "boccola"


# ------------------------------------------------------- round-trip su DB

@needs_pg
class TestRoundTripDB:
    def test_seed_load_equivalente_al_json(self, conn):
        from src.registry.synonyms import (init_schema, load_seed_config,
                                           load_synonyms, upsert_concept)
        init_schema(conn)
        cfg = load_seed_config(SEED_PATH)
        for slug, c in cfg.items():
            upsert_concept(conn, slug, c)
        assert load_synonyms(conn) == cfg

    def test_upsert_idempotente_e_llm_proposed_sopravvive(self, conn):
        from src.registry.synonyms import (init_schema, load_seed_config,
                                           load_synonyms, upsert_concept)
        init_schema(conn)
        cfg = load_seed_config(SEED_PATH)
        for slug, c in cfg.items():
            upsert_concept(conn, slug, c)
        # proposta LLM non approvata: invisibile al load, sopravvive al re-seed
        conn.execute(
            "INSERT INTO synonym_terms (concept_id, term, lang, source, approved) "
            "SELECT id, 'proposta llm', 'IT', 'llm_proposed', false "
            "FROM synonym_concepts WHERE slug = 'vite'")
        conn.commit()
        prima = load_synonyms(conn)
        assert ["proposta llm", "IT"] not in prima["vite"]["terms"]
        upsert_concept(conn, "vite", cfg["vite"])  # re-seed del concetto
        n = conn.execute("SELECT count(*) FROM synonym_terms "
                         "WHERE term = 'proposta llm'").fetchone()[0]
        assert n == 1 and load_synonyms(conn) == prima

    def test_lingua_non_ammessa_rifiutata(self, conn):
        from src.registry.synonyms import init_schema, upsert_concept
        init_schema(conn)
        with pytest.raises(ValueError, match="lingua"):
            upsert_concept(conn, "x", {"terms": [["bobina", "PT"]]})
