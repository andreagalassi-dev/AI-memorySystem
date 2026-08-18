"""Guardia deterministica sulle CITAZIONI (non-LLM).

Caso reale che l'ha motivata (golden gate `C_EN_03`, query "code 874300"): il
modello ha scritto `(rotax_912_914_IPC, pag. 874300)` — ha messo il CODICE
RICAMBIO al posto del numero di pagina — e il sistema ha accettato una
citazione a una pagina mai recuperata, restando ALTA. Sbagliato in silenzio:
esattamente cio' che il progetto vieta.

Regola: una citazione vale solo se la coppia (documento, pagina) e' fra quelle
EFFETTIVAMENTE recuperate e passate al modello. Confronto per uguaglianza sul
nome intero (MAI sottostringa: lezione del fix `ac8da1a`) e pagina intera.
Se dopo lo scarto non resta alcuna citazione, la risposta degrada al rifiuto
gia' previsto (`REFUSAL` -> `reconcile` -> BASSA), senza inventare nuovi stati.
"""

from __future__ import annotations

from src.graph.pipeline import Deps, Evidence, reconcile, synthesis_node
from src.retrieval.confidence import Thresholds
from src.retrieval.synthesis import REFUSAL, verify_citations

# L'evidenza REALE del caso C_EN_03: la pagina giusta e' la 84.
EV84 = {"type": "table_row", "doc": "rotax_912_914_IPC", "page": 84, "lang": "EN",
        "code": "874300", "desc": "OIL PUMP", "qty": 1, "price": None}
EV_OTHER = {"type": "text", "doc": "lycoming_IGO540_PC116", "page": 12,
            "lang": "EN", "text": "Oil system overview."}

ANSWER_BAD = ("The oil pump is part 874300 (rotax_912_914_IPC, pag. 874300).")
ANSWER_GOOD = ("The oil pump is part 874300 (rotax_912_914_IPC, pag. 84).")


class TestCitazioneNonRecuperata:
    def test_caso_reale_codice_al_posto_della_pagina(self):
        """C_EN_03: pag. 874300 non e' fra le pagine recuperate -> rifiutata."""
        r = verify_citations(ANSWER_BAD, [EV84])
        assert r.dropped == [("rotax_912_914_IPC", 874300)]
        assert r.kept == []
        assert "874300)" not in r.text  # lo span falso sparisce dal testo

    def test_citazione_recuperata_passa_intatta(self):
        r = verify_citations(ANSWER_GOOD, [EV84])
        assert r.kept == [("rotax_912_914_IPC", 84)]
        assert r.dropped == []
        assert r.text == ANSWER_GOOD

    def test_pagina_giusta_documento_sbagliato(self):
        t = "Vedi (lycoming_IGO540_PC116, pag. 84)."
        r = verify_citations(t, [EV84, EV_OTHER])
        assert r.dropped == [("lycoming_IGO540_PC116", 84)] and r.kept == []

    def test_pagina_fuori_di_uno(self):
        t = "Vedi (rotax_912_914_IPC, pag. 85)."
        r = verify_citations(t, [EV84])
        assert r.dropped == [("rotax_912_914_IPC", 85)] and r.kept == []

    def test_nome_documento_mai_per_sottostringa(self):
        # ne' il prefisso ne' il super-nome valgono: solo l'uguaglianza (ac8da1a)
        assert verify_citations("(rotax_912, pag. 84).", [EV84]).kept == []
        assert verify_citations("(rotax_912_914_IPC_v2, pag. 84).", [EV84]).kept == []

    def test_differenza_di_maiuscole_tollerata(self):
        # variazione di trascrizione innocua, non un documento diverso
        r = verify_citations("(ROTAX_912_914_IPC, pag. 84).", [EV84])
        assert r.kept == [("ROTAX_912_914_IPC", 84)] and r.dropped == []

    def test_mix_tiene_la_valida_e_scarta_la_falsa(self):
        t = ("Pompa (rotax_912_914_IPC, pag. 84); schema "
             "(rotax_912_914_IPC, pag. 874300).")
        r = verify_citations(t, [EV84])
        assert r.kept == [("rotax_912_914_IPC", 84)]
        assert r.dropped == [("rotax_912_914_IPC", 874300)]
        assert "pag. 84" in r.text and "874300)" not in r.text

    def test_nessuna_citazione_niente_scarti(self):
        r = verify_citations("Non ho evidenza.", [EV84])
        assert r.kept == [] and r.dropped == [] and r.text == "Non ho evidenza."

    def test_evidenza_vuota_scarta_tutto(self):
        r = verify_citations(ANSWER_GOOD, [])
        assert r.kept == [] and r.dropped == [("rotax_912_914_IPC", 84)]

    def test_pagina_zero_e_una_pagina_valida(self):
        ev = {"type": "table_row", "doc": "listino_ricambi.xlsx", "page": 0,
              "lang": "IT", "code": "AB12", "desc": "vite"}
        r = verify_citations("Vite AB12 (listino_ricambi.xlsx, pag. 0).", [ev])
        assert r.kept == [("listino_ricambi.xlsx", 0)] and r.dropped == []


# --------------------------------------------------------- integrazione grafo

EV_OK = Evidence(doc="rotax_912_914_IPC", page=84, lang="EN", code="874300",
                 desc="OIL PUMP", text=None, qty=1, price=None,
                 type="table_row", status="clean", warnings=[], score=9.0)
T = Thresholds(tau_hi=0.5, tau_lo=0.2)


def _deps(out: str) -> Deps:
    return Deps(retrieve=lambda q: ([EV_OK], []),
                synthesize=lambda q, ev: out,
                catalog_lookup=lambda k: False,
                thresholds=T)


class TestSynthesisNode:
    def test_citazione_inventata_non_entra_nello_stato(self):
        st = {"query": "code 874300", "evidence": [EV_OK], "level": "ALTA"}
        out = synthesis_node(st, _deps(ANSWER_BAD))
        assert out["citations"] == []
        assert any("scartata" in a for a in out["audit"])

    def test_zero_citazioni_valide_degrada_al_rifiuto(self):
        st = {"query": "code 874300", "evidence": [EV_OK], "level": "ALTA"}
        out = synthesis_node(st, _deps(ANSWER_BAD))
        assert out["answer"] == REFUSAL
        # coerente con il meccanismo gia' esistente: REFUSAL -> BASSA
        fin = reconcile({**st, **out, "guardrail_gate": "PASS"})
        assert fin["final_level"] == "BASSA" and fin["final_answer"] == REFUSAL

    def test_citazione_valida_passa_e_non_sporca_l_audit(self):
        st = {"query": "code 874300", "evidence": [EV_OK], "level": "ALTA"}
        out = synthesis_node(st, _deps(ANSWER_GOOD))
        assert out["citations"] == [("rotax_912_914_IPC", 84)]
        assert out["answer"] == ANSWER_GOOD
        assert not any("scartata" in a for a in out["audit"])

    def test_risposta_senza_citazioni_resta_com_era(self):
        # nessuno scarto: il comportamento storico (0 citazioni) non cambia
        st = {"query": "x", "evidence": [EV_OK], "level": "MEDIA"}
        out = synthesis_node(st, _deps("La pompa e' presente."))
        assert out["answer"] == "La pompa e' presente." and out["citations"] == []

    def test_children_evidence_conta_come_recuperata(self):
        child = EV_OK._replace(page=85, code="874301", desc="GASKET")
        st = {"query": "x", "evidence": [EV_OK], "children_evidence": [child],
              "level": "ALTA"}
        out = synthesis_node(st, _deps("Guarnizione (rotax_912_914_IPC, pag. 85)."))
        assert out["citations"] == [("rotax_912_914_IPC", 85)]
