"""P4.5 — Test RBAC: ruolo obbligatorio tipizzato + filtro AND + leak asimmetrico.

Vincoli sotto test (SPEC §F.3): answer() esige un Role valido (mai None,
mai stringa libera); il grafo e' fail-closed anche su invocazione DIRETTA
senza ruolo (difesa in profondita' nel nodo d'ingresso); il filtro Qdrant
e' AND(versione, ruolo); un commerciale NON recupera documenti tecnici
(leak test asimmetrico, DoD P4).
"""

from __future__ import annotations

import pytest

from src.common.roles import Role
from src.graph.pipeline import Deps, Evidence, answer, build_graph
from src.retrieval.confidence import Thresholds
from src.retrieval.synthesis import REFUSAL
from tests.conftest import needs_pg, needs_qdrant

T = Thresholds(tau_hi=0.5, tau_lo=0.2)

EV = Evidence(doc="rotax_912_914_IPC", page=22, lang="EN", code="877615",
              desc="PISTON PIN", text=None, qty=1, price=None, type="table_row",
              status="ok", warnings=[], score=9.0, version_status="current",
              doc_version=1)
EV2 = EV._replace(code="877616", score=2.0)


def make_deps(retrieve=None, synthesize=None, catalog=None):
    return Deps(
        retrieve=retrieve or (lambda q: [EV, EV2]),
        synthesize=synthesize or (
            lambda q, ev: "Lo spinotto è 877615 (rotax_912_914_IPC, pag. 22)."),
        catalog_lookup=catalog or (lambda k: True),
        thresholds=T,
    )


# ---------------------------------------------------------- API answer()

class TestAnswerRoleObbligatorio:
    def test_senza_ruolo_typeerror(self):
        with pytest.raises(TypeError):
            answer("query")  # type: ignore[call-arg]

    def test_ruolo_none_typeerror(self):
        with pytest.raises(TypeError):
            answer("query", None, deps=make_deps())  # type: ignore[arg-type]

    def test_ruolo_ignoto_valueerror(self):
        with pytest.raises(ValueError):
            answer("query", "admin", deps=make_deps())

    def test_ruolo_normalizzato_e_propagato(self):
        r = answer("che codice ha lo spinotto?", "  Tecnico ", deps=make_deps())
        assert r["final_level"] in ("ALTA", "MEDIA")
        assert any("tecnico" in a for a in r["audit"])  # ruolo in audit

    def test_ruolo_enum_accettato(self):
        r = answer("che codice ha lo spinotto?", Role.TECNICO, deps=make_deps())
        assert "877615" in r["final_answer"]


# ------------------------------------- difesa in profondita' (nodo ingresso)

class TestIngressFailClosed:
    def test_invoke_diretto_senza_ruolo_rifiutato(self):
        seen = {"retrieve": 0, "synth": 0}
        def spy_retrieve(q):
            seen["retrieve"] += 1
            return [EV]
        def spy_synth(q, ev):
            seen["synth"] += 1
            return "x"
        g = build_graph(make_deps(retrieve=spy_retrieve, synthesize=spy_synth))
        r = g.invoke({"query": "che codice ha lo spinotto?"})  # NIENTE role
        assert r["final_level"] == "BASSA"
        assert "ruolo" in r["final_answer"].lower()
        assert seen == {"retrieve": 0, "synth": 0}   # ne' retrieval ne' LLM

    def test_invoke_diretto_ruolo_spazzatura_rifiutato(self):
        g = build_graph(make_deps())
        r = g.invoke({"query": "x", "role": "superadmin"})
        assert r["final_level"] == "BASSA" and "ruolo" in r["final_answer"].lower()


# ------------------------------------------------ leak test asimmetrico

@needs_qdrant
class TestLeakAsimmetrico:
    """Query codice 877615: vive SOLO in doc engines (ruolo: solo tecnico)."""

    def _client(self):
        from src.backend.clients.qdrant_client import get_qdrant_client
        return get_qdrant_client()

    def test_search_tecnico_trova_commerciale_zero_hit(self):
        from src.retrieval.search import access_filter, search
        qc = self._client()
        tec = search(qc, "877615", "routed", limit=10,
                     query_filter=access_filter("current", Role.TECNICO))
        com = search(qc, "877615", "routed", limit=10,
                     query_filter=access_filter("current", Role.COMMERCIALE))
        assert tec and all("tecnico" in h.payload["ruoli_consentiti"] for h in tec)
        assert com == []                              # IL leak test: zero hit

    def test_e2e_commerciale_rifiutato_senza_llm(self):
        # percorso negativo: BASSA rifiuta PRIMA della synthesis (P3.5),
        # quindi non dipende da Ollama
        r = answer("877615", "commerciale")
        assert r["final_level"] == "BASSA"
        assert r["final_answer"] == REFUSAL

    @needs_pg
    def test_perimetro_conteggi_da_registro(self):
        from qdrant_client import models

        from src.backend.clients.postgres_client import get_pg_conn
        from src.registry.registry import export_backfill
        qc = self._client()

        def count(flt):
            return qc.count("aike_chunks", exact=True, count_filter=flt).count

        def role_filter(role):
            return models.Filter(must=[
                models.FieldCondition(key="version_status",
                                      match=models.MatchValue(value="current")),
                models.FieldCondition(key="ruoli_consentiti",
                                      match=models.MatchValue(value=role))])

        conn = get_pg_conn()
        export = export_backfill(conn)
        conn.close()
        total = qc.count("aike_chunks", exact=True).count
        assert count(role_filter("tecnico")) == total      # tecnico vede tutto
        expected_com = sum(
            count(models.Filter(must=[models.FieldCondition(
                key="doc", match=models.MatchValue(value=stem))]))
            for stem, rec in export.items()
            if Role.COMMERCIALE in rec.ruoli)
        got_com = count(role_filter("commerciale"))
        assert got_com == expected_com and 0 < got_com < total