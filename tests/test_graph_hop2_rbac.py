"""P4.5/A6 — l'Hop 2 del GRAFO deve filtrare i figli per ruolo, come quello di
`src/retrieval/search.py`.

Esistono due implementazioni dell'Hop 2 (vedi il commento su `_apply_hop2` in
`src/graph/pipeline.py`): questa suite copre quella del percorso di produzione
del grafo, l'unica che finisce in `combined_ev` e quindi nel prompt di sintesi.
Una divergenza di regola fra le due copie e' un bug, non una scelta.

Niente rete e niente LLM: Qdrant e' un finto che APPLICA davvero il filtro
ricevuto (cosi' il test misura il comportamento, non la forma del filtro) e
`search` e' sostituito da una spia che restituisce il padre assieme.
"""

from __future__ import annotations

import pytest
from qdrant_client import models

import src.retrieval.search as S
from src.common.roles import Role

# Dalla COSTANTE, non una stringa: l'invariante e' "i figli si leggono dalla
# collezione IN USO". Fissare il nome faceva fallire questo test allo scambio
# d'indice del 24/08, cioe' segnalava un cambiamento legittimo invece del
# difetto che deve sorvegliare (il filtro di ruolo sui figli).
from src.ingestion.embedding import COLLECTION

# Padre assieme visibile ANCHE al commerciale; figli riservati al solo tecnico.
PARENT = {"code": "KIT-1", "is_assembly": True, "doc": "d", "page": 1,
          "lang": "IT", "desc": "kit pompa", "type": "table_row", "status": "ok",
          "version_status": "current", "ruoli_consentiti": ["tecnico", "commerciale"]}
CHILD_TECNICO = {"code": "SEG-9", "parent_id": "KIT-1", "is_assembly": False,
                 "doc": "d", "page": 2, "lang": "IT", "desc": "segreto tecnico",
                 "type": "table_row", "status": "ok", "version_status": "current",
                 "ruoli_consentiti": ["tecnico"]}
CHILD_APERTO = {**CHILD_TECNICO, "code": "PUB-9", "desc": "componente pubblico",
                "ruoli_consentiti": ["tecnico", "commerciale"]}
CHILD_SUPERSEDED = {**CHILD_APERTO, "code": "OLD-9", "version_status": "superseded"}


def _match(payload: dict, cond: models.FieldCondition) -> bool:
    """Semantica Qdrant usata qui: MatchValue su lista = 'la lista contiene'."""
    got = payload.get(cond.key)
    want = cond.match.value
    return want in got if isinstance(got, list) else got == want


class FakeQdrant:
    """Registra i filtri visti e li applica al corpus, come farebbe Qdrant."""

    def __init__(self, corpus: list[dict]):
        self.corpus = corpus
        self.filtri: list[models.Filter] = []

    def scroll(self, collection, scroll_filter=None, limit=100, **kw):
        assert collection == COLLECTION
        self.filtri.append(scroll_filter)
        conds = list(scroll_filter.must or []) if scroll_filter else []
        out = [
            models.Record(id=i, payload=p, vector=None)
            for i, p in enumerate(self.corpus)
            if all(_match(p, c) for c in conds)
        ]
        return out[:limit], None


@pytest.fixture
def deps_con_figli(monkeypatch):
    """`deps.retrieve` di produzione con Qdrant finto. Ritorna (factory, fake)."""
    fake = FakeQdrant([PARENT, CHILD_TECNICO, CHILD_APERTO, CHILD_SUPERSEDED])

    import src.backend.clients.qdrant_client as QC
    monkeypatch.setattr(QC, "get_qdrant_client", lambda *a, **k: fake)

    def spia_search(client, query, strategy="routed", limit=10, **kw):
        # il padre e' visibile al ruolo interrogante: e' il caso che INNESCA
        # l'hop2. Cosa vede il ruolo sui FIGLI e' quello che il test misura.
        return [models.ScoredPoint(id=0, version=0, score=9.0, vector=None,
                                   payload=PARENT)]

    monkeypatch.setattr(S, "search", spia_search)

    from src.graph.pipeline import build_default_deps

    def factory(role: Role):
        # llm=object(): nessun Ollama, la sintesi non viene mai invocata.
        return build_default_deps(role, llm=object())

    return factory, fake


def _figli(deps) -> list[str]:
    # query a token singolo con cifre -> route 'code': niente reranker, niente torch
    _grouped, children = deps.retrieve("877615")
    return sorted(e.code for e in children)


class TestHop2RbacGrafo:
    def test_ruolo_inferiore_non_riceve_figli_riservati(self, deps_con_figli):
        """IL test: il commerciale raggiunge il padre, ma NON i figli tecnici."""
        factory, _fake = deps_con_figli
        codici = _figli(factory(Role.COMMERCIALE))
        assert "SEG-9" not in codici, (
            "leak RBAC: un figlio riservato al tecnico e' finito in combined_ev "
            f"per il commerciale (figli: {codici})")
        assert codici == ["PUB-9"]

    def test_simmetrico_i_figli_visibili_arrivano(self, deps_con_figli):
        """Falsifica il 'filtra tutto': il tecnico vede entrambi i figli."""
        factory, _fake = deps_con_figli
        assert _figli(factory(Role.TECNICO)) == ["PUB-9", "SEG-9"]

    def test_filtro_figli_in_and_con_parent_id(self, deps_con_figli):
        """Il filtro dei figli e' AND(parent_id, ruolo, versione)."""
        factory, fake = deps_con_figli
        _figli(factory(Role.COMMERCIALE))
        assert fake.filtri, "l'hop2 non ha nemmeno interrogato Qdrant"
        chiavi = {c.key: c.match.value for c in fake.filtri[-1].must}
        assert chiavi == {"parent_id": "KIT-1", "ruoli_consentiti": "commerciale",
                          "version_status": "current"}

    def test_figli_ereditano_lo_scope_di_versione_del_padre(self, deps_con_figli):
        """Un figlio 'superseded' non entra in una risposta costruita su 'current'."""
        factory, _fake = deps_con_figli
        assert "OLD-9" not in _figli(factory(Role.TECNICO))
