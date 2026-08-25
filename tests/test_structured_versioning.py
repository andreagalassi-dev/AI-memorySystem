"""R1 — L'avviso di versione nel ramo STRUCTURED, deterministico.

Il consumatore dell'avviso (`reconcile`: testo + cap di livello) esiste da P4.4/P7
ed e' testato in `test_graph_versioning.py` / `test_deleted_cascade.py`. Qui si
copre il PRODUTTORE che mancava sul ramo strutturato:

  1. `aggregate_price_variants` ignorava `deleted` (solo `superseded`): un prezzo
     letto da un documento ELIMINATO usciva con `obsoleto="no"`;
  2. `sql_node` non accendeva mai `obsolete_warning`/`deleted_warning`, quindi
     `reconcile` non appendeva nulla e non cappava il livello.

Vincolo dell'intervento: in produzione NON esiste oggi alcuna riga `superseded` o
`deleted` (0/147 documenti). Il cambiamento non deve alterare in alcun modo il
comportamento con righe `current` o `version_status` NULL — vedi
`TestZeroRegressione`, che e' la parte piu' importante di questo file.
"""

from __future__ import annotations

from decimal import Decimal

from src.graph.pipeline import Deps, reconcile, sql_node
from src.graph.tools.sql_tool import LookupRow, SqlParams
from src.retrieval.confidence import Thresholds
from src.retrieval.price_variants import aggregate_price_variants

_T = Thresholds(tau_hi=0.5, tau_lo=0.2)
CODE = "V09199300001"


def _row(valore, *, page=1, doc="listino_x", status=None, ingested="2023-01-01"):
    return LookupRow(valore=valore, doc_stem=doc, page=page,
                     version_status=status, ingested_at=ingested)


class _RowsExecutor:
    """Esecutore stub che ritorna righe arbitrarie (il FakeSqlExecutor del Port
    ne ritorna una sola per codice: qui servono i mix)."""

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[SqlParams] = []

    def run(self, params: SqlParams):
        self.calls.append(params)
        return list(self.rows)


def _deps(rows):
    return Deps(retrieve=lambda q: ([], []), synthesize=lambda q, ev: "n/a",
                catalog_lookup=lambda k: False, thresholds=_T,
                sql_query=_RowsExecutor(rows))


def _sql_out(rows, *, query="prezzo del codice"):
    return sql_node({"query": query, "code_token": CODE, "role": "tecnico"},
                    _deps(rows))


# --------------------------------------------------------------- price_variants

class TestAggregateStatiDiVersione:
    def test_deleted_e_avviso_di_eliminazione_non_di_sostituzione(self):
        ans = aggregate_price_variants([_row(Decimal("10.00"), status="deleted"),
                                        _row(Decimal("10.00"), page=2, status="deleted")])
        assert ans.principale.deleted is True
        # un ELIMINATO non deve essere declassato ad avviso di SOSTITUITO
        assert ans.principale.superseded is False

    def test_superseded_resta_avviso_di_sostituzione(self):
        ans = aggregate_price_variants([_row(Decimal("10.00"), status="superseded")])
        assert ans.principale.superseded is True
        assert ans.principale.deleted is False

    def test_gruppo_misto_current_piu_obsoleta_non_e_obsoleto(self):
        # Regola di GRUPPO (invariata da prima): il valore di prezzo e' marcato
        # obsoleto solo se TUTTE le righe che lo attestano lo sono. Qui una
        # fonte `current` conferma il valore -> il valore non e' obsoleto.
        # L'avviso all'utente scatta comunque, ma a livello di RISPOSTA (any):
        # vedi TestSqlNodeFlag.
        ans = aggregate_price_variants([_row(Decimal("10.00"), status="current"),
                                        _row(Decimal("10.00"), page=2, status="deleted")])
        assert ans.principale.deleted is False
        assert ans.principale.superseded is False

    def test_gruppo_tutto_obsoleto_ma_misto_vince_il_piu_grave(self):
        # superseded + deleted, nessuna riga current: il valore non e' attestato
        # da nulla di vivo. Fra i due stati vince il PIU' GRAVE (deleted, cap
        # BASSA), mai il piu' mite: mai declassare un eliminato a sostituito.
        ans = aggregate_price_variants([_row(Decimal("10.00"), status="superseded"),
                                        _row(Decimal("10.00"), page=2, status="deleted")])
        assert ans.principale.deleted is True
        assert ans.principale.superseded is False

    def test_ranking_il_prezzo_vivo_batte_quello_eliminato(self):
        rows = [
            _row(Decimal("99.00"), page=1, status="deleted"),
            _row(Decimal("99.00"), page=2, status="deleted"),
            _row(Decimal("99.00"), page=3, status="deleted"),
            _row(Decimal("12.50"), page=9, status="current"),
        ]
        ans = aggregate_price_variants(rows)
        # freq 3 vs 1: senza la regola di versione vincerebbe il 99.00 eliminato
        assert ans.principale.prezzo == Decimal("12.50")
        assert ans.varianti[0].deleted is True


class TestQuasiGiusti:
    """`version_status` e' vincolato a CHECK IN ('current','superseded','deleted')
    su `document_versions` (src/registry/schema.sql), ma il payload Qdrant non ha
    vincoli: si normalizza SOLO maiuscole/spazi, in UN punto solo
    (`normalize_version_status`). Nessun match per prefisso o fuzzy."""

    def test_maiuscole_e_spazi_sono_lo_stesso_stato(self):
        for raw in ("Deleted", "DELETED", "  deleted  "):
            ans = aggregate_price_variants([_row(Decimal("1.00"), status=raw)])
            assert ans.principale.deleted is True, raw
            assert ans.principale.superseded is False, raw

    def test_token_troncato_non_e_lo_stato_reale(self):
        for raw in ("delete", "deleted_at", "obsolete", "supersede", ""):
            ans = aggregate_price_variants([_row(Decimal("1.00"), status=raw)])
            assert ans.principale.deleted is False, raw
            assert ans.principale.superseded is False, raw

    def test_quasi_giusti_non_accendono_i_flag_del_grafo(self):
        for raw in ("delete", "obsolete", "supersede", ""):
            out = _sql_out([_row(Decimal("1.00"), status=raw)])
            assert "deleted_warning" not in out, raw
            assert "obsolete_warning" not in out, raw


# --------------------------------------------------------------- sql_node

class TestSqlNodeFlag:
    def test_riga_superseded_accende_obsolete_warning(self):
        out = _sql_out([_row(Decimal("10.00"), status="superseded")])
        assert out["obsolete_warning"] is True
        assert "deleted_warning" not in out

    def test_riga_deleted_accende_deleted_warning(self):
        out = _sql_out([_row(Decimal("10.00"), status="deleted")])
        assert out["deleted_warning"] is True
        assert "obsolete_warning" not in out

    def test_una_sola_riga_obsoleta_su_molte_basta(self):
        # Regola di RISPOSTA: ANY, non ALL. Le citazioni mostrate all'utente
        # sono TUTTE le righe usate (render_node: citations = [(doc,page) ...]):
        # se anche una sola punta a un documento non piu' valido, l'utente deve
        # saperlo. "Giusto oppure non lo so, mai sbagliato in silenzio".
        out = _sql_out([_row(Decimal("10.00"), page=1, status="current"),
                        _row(Decimal("10.00"), page=2, status="superseded")])
        assert out["obsolete_warning"] is True

    def test_deleted_prevale_su_superseded_nel_mix(self):
        out = _sql_out([_row(Decimal("10.00"), page=1, status="superseded"),
                        _row(Decimal("10.00"), page=2, status="deleted")])
        # entrambi accesi: `reconcile` gestisce `deleted_warning` per primo e
        # cappa a BASSA. L'avviso piu' grave non puo' essere perso.
        assert out["deleted_warning"] is True
        assert out["obsolete_warning"] is True

    def test_zero_righe_nessun_flag(self):
        out = _sql_out([])
        assert out["level"] == "BASSA"
        assert "obsolete_warning" not in out and "deleted_warning" not in out


# --------------------------------------------------------------- reconcile riusato

class TestReconcileRiusato:
    """Non si riscrive l'avviso: si accendono i flag e si INTERROGA `reconcile`,
    unica autorita' sul testo e sul cap di livello."""

    def test_superseded_cappa_ad_media_con_testo_sostituita(self):
        state = {"query": "prezzo", "code_token": CODE, "role": "tecnico"}
        state.update(_sql_out([_row(Decimal("10.00"), status="superseded")]))
        state.update({"answer": "Il codice costa 10,00 EUR.", "guardrail_gate": "PASS"})
        out = reconcile(state)
        assert out["final_level"] == "MEDIA"        # cap: mai ALTA da obsoleto
        assert "SOSTITUITA" in out["final_answer"]
        assert "ELIMINATO" not in out["final_answer"]

    def test_deleted_cappa_a_bassa_con_testo_eliminato(self):
        state = {"query": "prezzo", "code_token": CODE, "role": "tecnico"}
        state.update(_sql_out([_row(Decimal("10.00"), status="deleted")]))
        state.update({"answer": "Il codice costa 10,00 EUR.", "guardrail_gate": "PASS"})
        out = reconcile(state)
        assert out["final_level"] == "BASSA"
        assert "ELIMINATO" in out["final_answer"]


# --------------------------------------------------------------- render (XML + fallback)

class TestRenderNonMente:
    """L'attributo XML `obsoleto` NON e' l'avviso (nessuna regola di
    SYSTEM_RENDER obbliga il modello a riportarlo, ed e' proprio per questo che
    l'avviso vero e' deterministico in `reconcile`). Deve pero' smettere di
    dichiarare `no` per un prezzo che viene da un documento eliminato."""

    def test_attributo_obsoleto_si_anche_per_eliminato(self):
        from src.graph.render import build_render_prompt
        pa = aggregate_price_variants([_row(Decimal("10.00"), status="deleted")])
        assert 'obsoleto="si"' in build_render_prompt("prezzo", CODE, "prezzo", [], pa)

    def test_attributo_obsoleto_no_per_current(self):
        from src.graph.render import build_render_prompt
        pa = aggregate_price_variants([_row(Decimal("10.00"), status="current")])
        assert 'obsoleto="no"' in build_render_prompt("prezzo", CODE, "prezzo", [], pa)

    def test_fallback_deterministico_distingue_eliminato_da_vecchia_versione(self):
        # LLM che omette una variante -> fallback deterministico di render_node
        from src.graph.llm import FakeLLM
        from src.graph.pipeline import render_node
        from src.graph.tools.sql_tool import extract_params
        rows = [_row(Decimal("10.00"), page=1, status="deleted"),
                _row(Decimal("20.00"), page=2, status="deleted")]
        deps = Deps(retrieve=lambda q: ([], []), synthesize=lambda q, ev: "n/a",
                    catalog_lookup=lambda k: False, thresholds=_T,
                    llm=FakeLLM("Costa 10,00 EUR."), sql_query=_RowsExecutor(rows))
        out = render_node({"query": "prezzo", "code_token": CODE, "sql_rows": rows,
                           "sql_params": extract_params(CODE, "prezzo", "tecnico")}, deps)
        assert "DOCUMENTO ELIMINATO" in out["answer"]
        assert "VECCHIA VERSIONE" not in out["answer"]


# --------------------------------------------------------------- zero regressione

class TestZeroRegressione:
    """In produzione TUTTE le righe sono `current` (0 superseded / 0 deleted su
    147 documenti) e `version_status` puo' essere NULL (LEFT JOIN su
    `document_versions`). In questi due casi lo stato prodotto dev'essere
    identico a prima dell'intervento."""

    def test_sql_node_current_stato_invariato(self):
        out = _sql_out([_row(Decimal("15.50"), page=3, status="current")])
        assert set(out) == {"sql_params", "sql_rows", "evidence", "level", "audit"}
        assert out["level"] == "ALTA"

    def test_sql_node_null_stato_invariato(self):
        out = _sql_out([_row(Decimal("15.50"), page=3, status=None)])
        assert set(out) == {"sql_params", "sql_rows", "evidence", "level", "audit"}
        assert out["level"] == "ALTA"

    def test_reconcile_current_nessun_avviso_ne_cap(self):
        state = {"query": "prezzo", "code_token": CODE, "role": "tecnico"}
        state.update(_sql_out([_row(Decimal("15.50"), page=3, status="current")]))
        state.update({"answer": "Il codice costa 15,50 EUR.", "guardrail_gate": "PASS"})
        out = reconcile(state)
        assert out == {"final_answer": "Il codice costa 15,50 EUR.", "final_level": "ALTA"}

    def test_aggregate_current_e_null_invariati(self):
        for status in ("current", None):
            rows = [_row(Decimal("19.50"), page=1, status=status, ingested="2023"),
                    _row(Decimal("19.50"), page=2, status=status, ingested="2023"),
                    _row(Decimal("18.20"), page=34, status=status, ingested="2023")]
            ans = aggregate_price_variants(rows)
            p = ans.principale
            assert (p.prezzo, p.pagine, p.doc_stems, p.superseded, p.freq) == (
                Decimal("19.50"), [1, 2], ["listino_x"], False, 2), status
            assert p.deleted is False, status
            assert len(ans.varianti) == 1 and ans.varianti[0].prezzo == Decimal("18.20")
            assert ans.varianti[0].superseded is False and ans.varianti[0].deleted is False

    def test_costruzione_posizionale_price_variant_retrocompat(self):
        from src.retrieval.price_variants import PriceVariant
        v = PriceVariant(Decimal("1.00"), [1], ["x"], False, 1)
        assert v.deleted is False
