"""Fase 1.3 Passo 3 — Test del Router `intent` (estensione deterministica).

`derive_intent` mappa l'esito di `classify_query` (+ whitelist keyword-prezzo) su un
intent chiuso `STRUCTURED | VISUAL | NARRATIVE`. Nessun LLM, nessun I/O: instradamento
auditabile e fail-safe. Copre i due caveat Fail-Closed decisi in design:
  - QUANTITA' -> NARRATIVE (il Semantic Layer 1.2 non ha colonna quantita');
  - keyword-prezzo SENZA codice -> NARRATIVE (niente da cercare in righe_listino).
Piu' la postura fail-safe su input degeneri (vuoto/emoji) -> sempre NARRATIVE.

`router_node` scrive l'intent nello stato (campo additivo): verifica che lo emetta
senza alterare il ramo `access_error`.
"""

from __future__ import annotations

from src.graph.pipeline import Deps, router_node
from src.retrieval.confidence import Thresholds
from src.retrieval.router import Intent, classify_query, derive_intent


def _intent(q: str) -> Intent:
    return derive_intent(classify_query(q), q)


class TestIntentType:
    def test_intent_e_un_enum_chiuso(self):
        assert {i.value for i in Intent} == {"structured", "visual", "narrative"}


class TestStructured:
    def test_bare_code_famiglia_e_structured(self):
        # Fast-Dispatch B2B: codice a secco = lookup listino (default prezzo)
        assert _intent("V09156800007") is Intent.STRUCTURED

    def test_bare_code_token_singolo_con_cifre_e_structured(self):
        assert _intent("0USBX4") is Intent.STRUCTURED

    def test_codice_in_frase_con_keyword_prezzo_e_structured(self):
        # route=mixed, ma la keyword-prezzo promuove a STRUCTURED
        assert _intent("prezzo del codice V09199300001") is Intent.STRUCTURED

    def test_keyword_prezzo_multilingua_promuove(self):
        assert _intent("was kostet il V09156800007") is Intent.STRUCTURED   # DE
        assert _intent("price of V09156800007") is Intent.STRUCTURED        # EN


class TestVisual:
    def test_marker_visivo_e_visual(self):
        assert _intent("mostrami l'esploso della pompa") is Intent.VISUAL

    def test_visual_prevale_anche_con_codice(self):
        assert _intent("mostrami l'esploso del codice V09199300001") is Intent.VISUAL


class TestNarrative:
    def test_domanda_naturale_e_narrative(self):
        assert _intent("come funziona la valvola") is Intent.NARRATIVE

    def test_codice_in_frase_senza_keyword_prezzo_e_narrative(self):
        # route=mixed how-to: il manuale, non il listino
        assert _intent("come si monta il codice V09199300001") is Intent.NARRATIVE


class TestCaveatFailClosed:
    def test_quantita_non_va_a_structured(self):
        # caveat 1: nessuna colonna quantita' in righe_listino -> mai STRUCTURED
        assert _intent("quante viti servono per il V09156800007") is Intent.NARRATIVE

    def test_keyword_prezzo_senza_codice_e_narrative(self):
        # caveat 2: niente code_token -> niente lookup possibile -> NARRATIVE
        assert _intent("quanto costa la spedizione") is Intent.NARRATIVE


class TestFailSafeInputDegeneri:
    def test_query_vuota_e_narrative(self):
        assert _intent("") is Intent.NARRATIVE

    def test_emoji_non_crasha_ed_e_narrative(self):
        assert _intent("😀🎉") is Intent.NARRATIVE

    def test_stringa_lunga_di_parole_e_narrative(self):
        assert _intent(("bla " * 500).strip()) is Intent.NARRATIVE


# --------------------------------------------------------------- router_node

_T = Thresholds(tau_hi=0.5, tau_lo=0.2)


def _deps() -> Deps:
    # router_node non usa i callable, ma Deps li richiede: stub minimi.
    return Deps(retrieve=lambda q: ([], []), synthesize=lambda q, ev: "",
                catalog_lookup=lambda k: False, thresholds=_T)


class TestRouterNodeEmetteIntent:
    def test_bare_code_emette_structured(self):
        out = router_node({"query": "V09156800007", "role": "commerciale"}, _deps())
        assert out["intent"] == "structured" and out["route"] == "code"

    def test_visual_emette_visual(self):
        out = router_node({"query": "mostrami l'esploso della pompa",
                           "role": "tecnico"}, _deps())
        assert out["intent"] == "visual"

    def test_naturale_emette_narrative(self):
        out = router_node({"query": "come funziona la valvola",
                           "role": "commerciale"}, _deps())
        assert out["intent"] == "narrative"

    def test_ruolo_invalido_resta_access_error_senza_intent(self):
        # ramo access_error invariato: nessun instradamento senza ruolo valido
        out = router_node({"query": "V09156800007", "role": "ospite"}, _deps())
        assert out.get("access_error") is True and "intent" not in out


class TestRouterFixLinguaggioPezzo:
    """Fase router-fix: linguaggio 'pezzo/assieme' senza marker esplicito -> VISUAL,
    con precedenza che NON ruba i lookup di prezzo/codice."""

    def test_assembly_senza_marker_va_visual(self):
        # era il bug Lycoming: 'assembly' andava a NARRATIVE con pagina sbagliata
        assert _intent("air inlet housing assembly of the Lycoming IGO-540 engine") is Intent.VISUAL

    def test_driving_assembly_va_visual(self):
        assert _intent("driving assembly of the Noblelift PTE20N pallet truck") is Intent.VISUAL

    def test_valve_kit_senza_prezzo_va_visual(self):
        assert _intent("self-leveling valve kit of the Gehl V270") is Intent.VISUAL

    def test_figure_marker_duro_va_visual(self):
        assert _intent("spare parts figure for Opacmare doors") is Intent.VISUAL

    def test_diagrammi_it_va_visual(self):
        assert _intent("diagrammi di portata del catalogo Bell Hydraulics") is Intent.VISUAL

    def test_kit_con_prezzo_NON_va_visual(self):
        # precedenza: il prezzo vince sul linguaggio-pezzo (resta narrative senza codice)
        assert _intent("prezzo del kit valvola") is Intent.NARRATIVE

    def test_kit_con_prezzo_e_codice_va_structured(self):
        # prezzo+codice -> structured, il 'kit' morbido NON lo dirotta
        assert _intent("prezzo del kit codice V09199300001") is Intent.STRUCTURED

    def test_frase_senza_linguaggio_pezzo_resta_narrative(self):
        assert _intent("come funziona la valvola di sfiato") is Intent.NARRATIVE
