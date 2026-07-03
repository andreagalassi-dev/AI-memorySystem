"""P2.7 — Test della classe visual del router."""

from __future__ import annotations

from src.retrieval.router import classify_query


def route(q):
    r = classify_query(q)
    return r.query_type, r.code_token


class TestVisualRouting:
    def test_marker_it(self):
        assert route("esploso del gruppo frizione")[0] == "visual"
        assert route("mostrami lo spaccato della pompa")[0] == "visual"
        assert route("schema elettrico del quadro")[0] == "visual"

    def test_marker_en_de_es(self):
        assert route("exploded view of the crankshaft")[0] == "visual"
        assert route("wiring diagram V270")[0] == "visual"
        assert route("Explosionszeichnung vom Rahmen")[0] == "visual"
        assert route("despiece de la bomba")[0] == "visual"

    def test_visual_con_codice_conserva_il_token(self):
        t, c = route("esploso del ricambio V09156800007")
        assert (t, c) == ("visual", "V09156800007")

    def test_nessun_falso_visual(self):
        assert route("guarnizione a disco per movimenti ceramici")[0] == "natural"
        assert route("codice 11282")[0] == "code"
        assert route("che prezzo ha il V09199300001?")[0] == "mixed"

    def test_reason_auditabile(self):
        r = classify_query("wiring diagram V270")
        assert "visivo" in r.reason
