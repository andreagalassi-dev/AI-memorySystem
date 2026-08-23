"""`AIKE_RERANK_TOPK`: profondita' del pool RRF che arriva al cross-encoder testuale.

Perche' esiste l'interruttore
-----------------------------
Sul metro narrativo il bersaglio corretto esiste nell'indice ma spesso sta oltre
la ventesima posizione RRF (misurato: R@10 0.393, R@20 0.554, R@100 0.750). Il
cross-encoder puo' riordinare solo cio' che vede, e vedeva venti candidati fissi
scritti nel sorgente. Per misurare pool piu' larghi senza riscrivere il codice a
ogni giro serve una variabile d'ambiente.

Perche' i test del percorso VERO
--------------------------------
`RERANK_TOPK` e' usato in DUE posti (`src/graph/pipeline.py` per la `retrieve` di
produzione, `src/retrieval/search.py` per la strategia `routed_rr`). Un
interruttore che ne copre uno solo fa misurare una configurazione che non esiste.
Verificare la funzione in isolamento NON lo prova: i due test marcati spiano il
`limit` che i due percorsi reali passano davvero a `search`.
"""

from __future__ import annotations

import pytest

import src.retrieval.rerank as rr
import src.retrieval.search as S
from src.common.roles import Role
from tests.conftest import needs_pg, needs_qdrant


@pytest.fixture(autouse=True)
def _env_pulito(monkeypatch):
    monkeypatch.delenv(rr.RERANK_TOPK_ENV, raising=False)


class TestLetturaEnv:
    def test_default_venti_se_assente(self):
        """Default 50 dal 22/08: misurato +3 risposte nette e 0 ALTA sbagliate,
        gate identico. Gli audit precedenti a quella data sono a 20."""
        assert rr.rerank_topk() == rr.RERANK_TOPK_DEFAULT == 50

    @pytest.mark.parametrize("raw,atteso", [("50", 50), ("100", 100), (" 35 ", 35)])
    def test_valore_valido_vince(self, monkeypatch, raw, atteso):
        monkeypatch.setenv(rr.RERANK_TOPK_ENV, raw)
        assert rr.rerank_topk() == atteso

    @pytest.mark.parametrize("raw", ["", "ciao", "20.5", "0", "-3"])
    def test_valore_rotto_degrada_al_default_non_a_zero(self, monkeypatch, raw):
        """Un pool a zero candidati sarebbe un rifiuto sistematico silenzioso:
        meglio tornare al riferimento noto."""
        monkeypatch.setenv(rr.RERANK_TOPK_ENV, raw)
        assert rr.rerank_topk() == 50

    def test_attributo_di_modulo_segue_lambiente(self, monkeypatch):
        """I punti che fanno `from src.retrieval.rerank import RERANK_TOPK`
        (inclusi gli script di misura) devono vedere il valore aggiornato."""
        assert rr.RERANK_TOPK == 50
        monkeypatch.setenv(rr.RERANK_TOPK_ENV, "70")
        assert rr.RERANK_TOPK == 70
        from src.retrieval.rerank import RERANK_TOPK
        assert RERANK_TOPK == 70

    def test_attributo_inesistente_resta_un_errore(self):
        with pytest.raises(AttributeError):
            rr.NON_ESISTE  # noqa: B018


def _spia_search(monkeypatch) -> list[tuple[str, int]]:
    """Registra (strategia, limit) di ogni chiamata a `search` e taglia corto.

    Il ritorno vuoto e' voluto: entrambi i percorsi escono subito su pool vuoto,
    quindi il cross-encoder non viene mai caricato e il test resta veloce.
    """
    visti: list[tuple[str, int]] = []
    reale = S.search

    def spia(client, query, strategy="routed", limit=10, **kw):
        visti.append((strategy, limit))
        if strategy == "routed":
            return []
        return reale(client, query, strategy, limit, **kw)

    monkeypatch.setattr(S, "search", spia)
    return visti


@needs_pg
@needs_qdrant
class TestPercorsiReali:
    """I due usi reali, non un oggetto costruito a mano."""

    @pytest.mark.parametrize("env,atteso", [(None, 50), ("20", 20)])
    def test_retrieve_di_produzione_usa_il_pool_configurato(self, monkeypatch, env, atteso):
        """`src/graph/pipeline.py` — la `deps.retrieve` che usano UI, gate e metro."""
        if env is not None:
            monkeypatch.setenv(rr.RERANK_TOPK_ENV, env)
        visti = _spia_search(monkeypatch)

        from src.graph.pipeline import build_default_deps

        deps = build_default_deps(Role.TECNICO, llm=object())
        ev, _children = deps.retrieve("come si regola la pressione della pompa?")

        assert ("routed", atteso) in visti, f"limit visti: {visti}"
        assert ev == []

    @pytest.mark.parametrize("env,atteso", [(None, 50), ("20", 20)])
    def test_strategia_routed_rr_usa_il_pool_configurato(self, monkeypatch, env, atteso):
        """`src/retrieval/search.py` — l'altro punto d'uso, quello degli script."""
        if env is not None:
            monkeypatch.setenv(rr.RERANK_TOPK_ENV, env)
        reale = S.search
        visti = _spia_search(monkeypatch)

        from src.backend.clients.qdrant_client import get_qdrant_client

        out = reale(get_qdrant_client(), "come si regola la pressione della pompa?",
                    "routed_rr", 10)

        assert ("routed", atteso) in visti, f"limit visti: {visti}"
        assert out == []
