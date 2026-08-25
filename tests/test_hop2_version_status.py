"""L'Hop 2 di `src/retrieval/search.py` prometteva l'RBAC e perdeva la VERSIONE.

`apply_hop2` filtrava i figli di un assieme su `parent_id` + `ruoli_consentiti`
e basta: un figlio `superseded` (o `deleted`) di un padre `current` rientrava
nella distinta. E' l'ultimo membro della famiglia trovata il 25/08 (1083d71,
3938c2e, adc8449) e ha la stessa forma delle altre — **un percorso che promette
una garanzia e ne mantiene solo meta'**.

Va detta l'ampiezza vera, misurata sull'indice vivo (aike_chunks_v2, 25/08):
199689 punti su 199689 sono `version_status='current'`, zero `superseded`, zero
`deleted`; dei 961 punti con `parent_id` non nullo, ZERO sono non-`current`.
Oggi il difetto ha bersaglio VUOTO. Resta un difetto: il giorno in cui una
re-ingestion marca un documento come sostituito (il grafo lo prevede: interroga
`current`, poi `superseded`/`deleted` in fallback) questo percorso mescolerebbe
componenti obsoleti dentro una distinta data per corrente, senza che l'avviso
di obsolescenza — deciso sul PADRE — si applichi a loro.

L'altra copia della regola, `_apply_hop2` dentro `src/graph/pipeline.py`, era
gia' corretta: estende con `model_copy` il filtro d'accesso completo, quindi si
porta dietro ANCHE la versione. Il monito scritto li' ("una divergenza fra le
due e' un bug, non una scelta") descriveva esattamente questa divergenza.

Disciplina dei test (quella di `tests/test_delete_document_points.py`): il finto
di Qdrant non guarda la FORMA del filtro, lo APPLICA a un corpus vero. Cosi'
"il figlio sostituito non entra" e' una misura del risultato, non l'ispezione
di un argomento — e un filtro giusto costruito e poi non passato non passerebbe.
"""

from __future__ import annotations

import pytest
from qdrant_client import models

import src.retrieval.search as S
from src.common.roles import Role

# Un assieme corrente e i suoi quattro figli. Le quattro righe esistono per
# separare le due garanzie: la VERSIONE (A vs B vs C) e il RUOLO (A vs D).
PADRE = {"id": 1, "code": "KIT-1", "is_assembly": True,
         "ruoli_consentiti": ["tecnico"], "version_status": "current"}
FIGLI = [
    # A: l'unico che deve entrare per il tecnico nello scope 'current'.
    {"id": 10, "code": "FIG-A", "parent_id": "KIT-1",
     "ruoli_consentiti": ["tecnico"], "version_status": "current"},
    # B: il bersaglio del difetto — ruolo giusto, versione sbagliata.
    {"id": 11, "code": "FIG-B", "parent_id": "KIT-1",
     "ruoli_consentiti": ["tecnico"], "version_status": "superseded"},
    # C: l'altro scope non-corrente. Un documento cancellato che rientra come
    # componente e' peggio di uno sostituito, non meglio.
    {"id": 12, "code": "FIG-C", "parent_id": "KIT-1",
     "ruoli_consentiti": ["tecnico"], "version_status": "deleted"},
    # D: versione giusta, ruolo altrui. Serve a impedire la regressione
    # speculare — sostituire una garanzia con l'altra invece di sommarle.
    {"id": 13, "code": "FIG-D", "parent_id": "KIT-1",
     "ruoli_consentiti": ["commerciale"], "version_status": "current"},
    # E: figlio di un ALTRO assieme. Il filtro su parent_id deve restare.
    {"id": 14, "code": "FIG-E", "parent_id": "KIT-2",
     "ruoli_consentiti": ["tecnico"], "version_status": "current"},
]


def _match(payload: dict, cond: models.FieldCondition) -> bool:
    """Semantica Qdrant usata qui: MatchValue su lista = 'la lista contiene'."""
    got = payload.get(cond.key)
    want = cond.match.value
    return want in got if isinstance(got, list) else got == want


class FakeQdrant:
    """`.scroll` APPLICA il filtro ricevuto al corpus e registra i filtri visti.

    Un filtro assente (`None`) fa passare tutto: e' il comportamento reale di
    Qdrant, ed e' cio' che rende il test capace di fallire davvero.
    """

    def __init__(self, corpus: list[dict]):
        self.corpus = corpus
        self.filtri: list[models.Filter | None] = []

    def scroll(self, collection, scroll_filter=None, limit=100, **kw):
        self.filtri.append(scroll_filter)
        if scroll_filter is None:
            passati = list(self.corpus)
        else:
            conds = list(scroll_filter.must or [])
            passati = [p for p in self.corpus
                       if all(_match(p, c) for c in conds)]
        return ([models.Record(id=p["id"], payload=p, vector=None)
                 for p in passati[:limit]], None)


def _hit(payload: dict) -> models.ScoredPoint:
    return models.ScoredPoint(id=payload["id"], version=0, score=1.0,
                              payload=payload, vector=None)


@pytest.fixture
def qc() -> FakeQdrant:
    return FakeQdrant([PADRE, *FIGLI])


def _codici(children) -> set[str]:
    return {(h.payload or {}).get("code") for h in children}


class TestVersioneNeiFigli:
    """Il difetto e la sua correzione, misurati sul RISULTATO."""

    def test_il_figlio_sostituito_non_entra(self, qc):
        _, children, _ = S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO)
        assert "FIG-B" not in _codici(children), (
            "un figlio 'superseded' e' rientrato nella distinta di un padre "
            "'current': l'Hop 2 di search.py non filtra su version_status")

    def test_il_figlio_cancellato_non_entra(self, qc):
        _, children, _ = S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO)
        assert "FIG-C" not in _codici(children)

    def test_il_figlio_corrente_entra_ancora(self, qc):
        """Il rovescio necessario: una guardia che blocca tutto non e' una
        guardia, e' un guasto."""
        _, children, _ = S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO)
        assert "FIG-A" in _codici(children)

    def test_solo_il_figlio_corrente_e_del_ruolo(self, qc):
        _, children, _ = S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO)
        assert _codici(children) == {"FIG-A"}


class TestIlRuoloRestaApplicato:
    """Le due garanzie si SOMMANO: correggere la versione non deve diluire
    l'RBAC gia' presente (era l'unica cosa che quel filtro faceva bene)."""

    def test_il_figlio_di_un_altro_ruolo_non_entra(self, qc):
        _, children, _ = S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO)
        assert "FIG-D" not in _codici(children)

    def test_un_ruolo_diverso_vede_i_suoi_figli(self, qc):
        _, children, _ = S.apply_hop2(qc, [_hit(PADRE)], Role.COMMERCIALE)
        assert _codici(children) == {"FIG-D"}

    def test_il_filtro_porta_tutte_e_tre_le_condizioni(self, qc):
        S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO)
        assert qc.filtri, "Hop 2 non ha nemmeno interrogato Qdrant"
        chiavi = {c.key: c.match.value for c in (qc.filtri[-1].must or [])}
        assert chiavi.get("parent_id") == "KIT-1"
        assert chiavi.get("ruoli_consentiti") == "tecnico"
        assert chiavi.get("version_status") == "current"

    def test_il_figlio_di_un_altro_assieme_non_entra(self, qc):
        _, children, _ = S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO)
        assert "FIG-E" not in _codici(children)


class TestRecordSenzaVersion:
    """Trovato dal finto FEDELE, non ipotizzato: `.scroll` restituisce
    `models.Record`, che NON ha il campo `version` (id/payload/vector/
    shard_key/order_value). `apply_hop2` faceva `r.version` e sollevava
    AttributeError contro Qdrant VERO — verificato il 25/08 sull'indice vivo,
    assieme `14557` con 3 figli. Cioe': l'Hop 2 di questo modulo non ha mai
    potuto restituire un figlio in produzione, e `get_bom`/`export_bom.py`
    esplodevano su qualunque assieme con figli. Il finto lo ha preso perche'
    costruisce `models.Record` veri invece di un oggetto comodo con gli
    attributi che servono al codice sotto test.
    """

    def test_i_figli_si_costruiscono_da_un_record_vero(self, qc):
        assert not hasattr(models.Record(id=1, payload={}, vector=None), "version")
        _, children, _ = S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO)
        assert children, "Hop 2 non ha prodotto figli da un Record reale"
        assert children[0].version == 0

    def test_i_figli_ereditano_lo_score_del_padre(self, qc):
        padre = _hit(PADRE)
        padre.score = 0.42
        _, children, _ = S.apply_hop2(qc, [padre], Role.TECNICO)
        assert [c.score for c in children] == [0.42]


class TestScopeEsplicito:
    """Lo scope e' un PARAMETRO, non una costante: il grafo interroga
    `superseded`/`deleted` in fallback e questo percorso deve poter fare lo
    stesso senza reintrodurre il difetto al contrario."""

    def test_default_e_current(self, qc):
        S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO)
        chiavi = {c.key: c.match.value for c in (qc.filtri[-1].must or [])}
        assert chiavi.get("version_status") == "current"

    def test_scope_superseded_seleziona_il_figlio_sostituito(self, qc):
        _, children, _ = S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO,
                                      version_status="superseded")
        assert _codici(children) == {"FIG-B"}


class TestComportamentiInvariati:
    """Cio' che la correzione NON deve cambiare."""

    def test_assieme_senza_figli_avvisa_ancora(self, qc):
        solo_padre = FakeQdrant([PADRE])
        _, children, warning = S.apply_hop2(solo_padre, [_hit(PADRE)], Role.TECNICO)
        assert children == []
        assert warning == "Assembly_Has_No_Children"

    def test_un_figlio_trovato_non_avvisa(self, qc):
        _, _, warning = S.apply_hop2(qc, [_hit(PADRE)], Role.TECNICO)
        assert warning is None

    def test_hit_non_assieme_non_interroga_qdrant(self, qc):
        non_assieme = dict(PADRE, is_assembly=False)
        grouped, children, warning = S.apply_hop2(qc, [_hit(non_assieme)], Role.TECNICO)
        assert (children, warning) == ([], None)
        assert qc.filtri == [], "Hop 2 ha interrogato Qdrant senza un assieme"

    def test_hop1_dedup_per_codice_invariato(self, qc):
        doppio = dict(PADRE, id=2)
        grouped, _, _ = S.apply_hop2(qc, [_hit(PADRE), _hit(doppio)], Role.TECNICO)
        assert len(grouped) == 1


class TestPropagazioneDaSearchUnified:
    """`search_unified` ha gia' un `version_status` (3938c2e): la ricerca
    principale e l'Hop 2 devono interrogare lo STESSO scope, altrimenti la
    risposta mescola due versioni dentro lo stesso messaggio."""

    @pytest.fixture
    def spia(self, monkeypatch):
        chiamate: list[models.Filter | None] = []

        def _search(client, query, strategy, limit=10, query_filter=None,
                    extra_terms=None):
            chiamate.append(query_filter)
            return [_hit(PADRE)]

        monkeypatch.setattr(S, "search", _search)
        return chiamate

    def test_lo_scope_di_default_arriva_all_hop2(self, qc, spia):
        S.search_unified(qc, "distinta del kit", role=Role.TECNICO)
        chiavi = {c.key: c.match.value for c in (qc.filtri[-1].must or [])}
        assert chiavi.get("version_status") == "current"

    def test_lo_scope_esplicito_arriva_all_hop2(self, qc, spia):
        S.search_unified(qc, "distinta del kit", role=Role.TECNICO,
                         version_status="superseded")
        chiavi = {c.key: c.match.value for c in (qc.filtri[-1].must or [])}
        assert chiavi.get("version_status") == "superseded", (
            "la ricerca principale e l'Hop 2 hanno interrogato scope di "
            "versione DIVERSI nella stessa risposta")


class TestBomEreditaIlDefaultSicuro:
    """`src/retrieval/bom.py` ancora il PADRE a `access_filter('current', role)`
    e poi delega i figli a `apply_hop2`. Col default 'current' i due scope
    coincidono senza che `bom.py` debba dire nulla: una BOM dichiarata corrente
    non puo' contenere componenti sostituiti."""

    def test_la_bom_non_contiene_componenti_sostituiti(self, qc):
        from src.retrieval.bom import get_bom

        bom = get_bom(qc, "KIT-1", Role.TECNICO)
        codici = {r["code"] for r in bom}
        assert "FIG-B" not in codici and "FIG-C" not in codici
        assert codici == {"FIG-A"}

    def test_la_bom_del_ruolo_altrui_resta_filtrata(self, qc):
        from src.retrieval.bom import get_bom

        # Il padre e' tecnico-only: il commerciale non arriva nemmeno all'Hop 2.
        assert get_bom(qc, "KIT-1", Role.COMMERCIALE) == []
