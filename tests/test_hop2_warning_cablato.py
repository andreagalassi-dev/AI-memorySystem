"""L'avviso "assieme senza figli" deve nascere NEL GRAFO, non solo su carta.

Perche' questo file esiste
--------------------------
`confidence_level` ha da sempre `if s.hop2_warning: -> MEDIA`, ma il campo lo
valorizzava soltanto `src.retrieval.search.apply_hop2`, che in produzione non
viene mai chiamata: il grafo ha la sua `_apply_hop2` (dentro `answer()`), che i
figli li recupera e basta. Risultato: una distinta base VUOTA poteva uscire con
livello ALTA. Stessa classe di difetto di `singleton` (92eb955) e della falla
RBAC (7b5d1ff): guardia scritta, testata, verde — e mai cablata.

Per questo i test qui sotto NON chiamano `confidence_level`: fanno girare il
GRAFO REALE (`build_graph` + `invoke`) e guardano il livello finale. E' l'unico
modo di dimostrare che la guardia spara dove gira il sistema.

Bersaglio reale misurato sul corpus (aike_chunks_v2, 2026-08-23): 210 chunk
assieme -> 68 codici assieme distinti, di cui **6 senza alcun figlio** (78019,
V01442600007, V01442700007, V01442900007, V01443000007, V01455500007). Assiemi
e figli sono tutti `ruoli_consentiti = ['tecnico']`.
"""

from __future__ import annotations

import pytest

from src.graph.pipeline import Deps, Evidence, build_graph
from src.retrieval.confidence import Thresholds


DOC, PAGE = "hurth_hsw_630", 12
SYNTH = f"Il kit e' 78019 ({DOC}, pag. {PAGE})."


def _ev(code: str, score: float, *, is_assembly: bool = False) -> Evidence:
    return Evidence(doc=DOC, page=PAGE, lang="IT", code=code,
                    desc="KIT GUARNIZIONI", text=None, qty=1, price=None,
                    type="table_row", status="clean", warnings=[], score=score,
                    version_status="current", doc_version=1,
                    is_assembly=is_assembly)


def _deps(retrieve) -> Deps:
    return Deps(retrieve=retrieve,
                synthesize=lambda q, ev: SYNTH,
                catalog_lookup=lambda k: False,
                thresholds=Thresholds(tau_hi=0.9, tau_lo=0.0054))


def _gira(retrieve, query: str = "codice 78019") -> dict:
    return build_graph(_deps(retrieve)).invoke({"query": query, "role": "tecnico"})


class TestAvvisoAssiemeSenzaFigli:

    def test_assieme_senza_figli_non_puo_uscire_ALTA(self):
        """Il caso reale: 6 codici del corpus. Distinta vuota -> mai ALTA."""
        out = _gira(lambda *a, **k: ([_ev("78019", 9.0, is_assembly=True)], []))
        assert out["final_level"] == "MEDIA", (
            "un assieme di cui NON abbiamo recuperato nessun componente e' uscito "
            f"a livello {out['final_level']}: la distinta base e' vuota e il "
            "sistema la presenta come certa.\naudit:\n  "
            + "\n  ".join(out.get("audit") or []))

    def test_l_avviso_compare_nell_audit(self):
        """Non basta il livello: la ragione dev'essere tracciabile."""
        out = _gira(lambda *a, **k: ([_ev("78019", 9.0, is_assembly=True)], []))
        audit = " ".join(out.get("audit") or [])
        assert "Assembly_Has_No_Children" in audit, (
            "livello corretto ma nessuna traccia del perche' nell'audit:\n  "
            + "\n  ".join(out.get("audit") or []))

    def test_assieme_CON_figli_resta_ALTA(self):
        """La guardia non deve declassare i 62 assiemi sani su 68."""
        out = _gira(lambda *a, **k: ([_ev("78019", 9.0, is_assembly=True)],
                                     [_ev("77001", 9.0)]))
        assert out["final_level"] == "ALTA", (
            "declassato un assieme che i figli ce li ha: falso allarme.\naudit:\n  "
            + "\n  ".join(out.get("audit") or []))

    def test_riga_normale_senza_figli_non_e_un_assieme(self):
        """Una riga ricambio non ha figli per definizione: nessun avviso."""
        out = _gira(lambda *a, **k: ([_ev("78019", 9.0)], []))
        assert out["final_level"] == "ALTA"
        assert "Assembly_Has_No_Children" not in " ".join(out.get("audit") or [])

    def test_assieme_senza_codice_non_avvisa(self):
        """Senza codice i figli non si cercano nemmeno (`_apply_hop2`): avvisare
        di una distinta vuota sarebbe un allarme su una domanda mai posta."""
        out = _gira(lambda *a, **k: ([_ev(None, 9.0, is_assembly=True)], []),
                    query="a cosa serve il kit guarnizioni")
        assert "Assembly_Has_No_Children" not in " ".join(out.get("audit") or [])

    def test_anche_il_fallback_sull_obsoleto_avvisa(self):
        """Il declassamento non deve saltare sui rami di fallback: la distinta
        vuota di un documento SOSTITUITO e' vuota esattamente allo stesso modo."""
        deps = _deps(lambda *a, **k: ([], []))
        deps = Deps(retrieve=deps.retrieve, synthesize=deps.synthesize,
                    catalog_lookup=deps.catalog_lookup, thresholds=deps.thresholds,
                    retrieve_superseded=lambda *a, **k: (
                        [_ev("78019", 9.0, is_assembly=True)], []))
        out = build_graph(deps).invoke({"query": "codice 78019", "role": "tecnico"})
        assert "Assembly_Has_No_Children" in " ".join(out.get("audit") or []), (
            "il fallback sull'obsoleto ricalcola la confidenza ma senza l'avviso:\n  "
            + "\n  ".join(out.get("audit") or []))
