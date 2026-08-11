"""Epica F.1a — test della riformulazione PURA (DB-free, no rete, LLM FINTO).

Copre: parita' a vuoto (nessun LLM), risoluzione accettata, guardia VERBATIM
(codice inventato -> scartato), non-fatale (LLM solleva), no-op (query gia'
autonoma). L'LLM e' il `FakeLLM` deterministico (handler/response/error).
"""

from __future__ import annotations

from src.graph.context import ConversationContext, ContextAssembler, Turn
from src.graph.contextualize import ResolvedQuery, contextualize_query
from src.graph.llm import FakeLLM


def _ctx(*pairs: tuple[str, str]) -> ConversationContext:
    return ContextAssembler().build(
        [{"role": r, "content": c} for r, c in pairs])


# ------------------------------------------------------------ parita' a vuoto

def test_context_none_no_llm_call():
    llm = FakeLLM("NON DEVE ESSERE CHIAMATO")
    rq = contextualize_query("prezzo del codice ABC123", None, llm)
    assert rq.resolved == "prezzo del codice ABC123"
    assert rq.changed is False and rq.reason == "no-context"
    assert llm.calls == []  # NIENTE LLM


def test_context_empty_no_llm_call():
    llm = FakeLLM("NON DEVE ESSERE CHIAMATO")
    rq = contextualize_query("prezzo del codice ABC123",
                             ConversationContext(), llm)
    assert rq.resolved == "prezzo del codice ABC123"
    assert rq.changed is False and rq.reason == "no-context"
    assert llm.calls == []


# ------------------------------------------------------------ risoluzione OK

def test_resolution_accepted_verbatim_id_in_history():
    ctx = _ctx(("user", "prezzo del modello ABC123"),
               ("assistant", "Il modello ABC123 costa 10 EUR."))
    llm = FakeLLM("prezzo del modello ABC123 nuovo")
    rq = contextualize_query("e per quello nuovo?", ctx, llm)
    assert rq.changed is True and rq.reason == "resolved"
    assert rq.resolved == "prezzo del modello ABC123 nuovo"
    assert rq.original == "e per quello nuovo?"
    assert len(llm.calls) == 1  # LLM interrogato una volta


# ------------------------------------------------------------ guardia VERBATIM

def test_invented_id_rewrite_discarded():
    # ZZ999 NON compare ne' nella history ne' nella query -> inventato.
    ctx = _ctx(("user", "prezzo del modello ABC123"))
    llm = FakeLLM("prezzo del modello ZZ999")
    rq = contextualize_query("e per quello nuovo?", ctx, llm)
    assert rq.resolved == "e per quello nuovo?"  # == original
    assert rq.changed is False
    assert "invented" in rq.reason
    assert "ZZ999" in rq.reason


def test_new_bare_number_conservatively_discarded():
    # TOKEN_RE (come nel router) spezza su virgola: "10,50" -> "10","50", che
    # sono identificatori (cifre, len>=2). Se un numero NUOVO non e' nel
    # contesto, la guardia M1 SCARTA il rewrite: direzione SICURA ("mai
    # sbagliato in silenzio"), si ricade sull'originale.
    ctx = _ctx(("user", "il codice ABC123"))
    llm = FakeLLM("il codice ABC123 costa 10,50")
    rq = contextualize_query("quanto costa?", ctx, llm)
    assert rq.changed is False and "invented" in rq.reason
    assert rq.resolved == "quanto costa?"


def test_number_present_in_history_passes_guard():
    # Se il numero e' GIA' nel contesto, il rewrite che lo riusa passa M1.
    ctx = _ctx(("user", "il codice ABC123 a 220 volt"))
    llm = FakeLLM("prezzo del codice ABC123 a 220 volt")
    rq = contextualize_query("e il prezzo?", ctx, llm)
    assert rq.changed is True and rq.reason == "resolved"


# --------------------------------- guardia VERBATIM: insiemi, non sottostringhe
# La guardia M1 confronta INSIEMI di identificatori CANONICALIZZATI (stessa
# `canonical()` del ramo SQL), non sottostringhe di un blob. Un prefisso/suffisso
# /frammento di un codice del dialogo NON e' quel codice: e' un'invenzione, e a
# valle potrebbe risolvere su un record REALE ma sbagliato (silent-wrong ALTA).

def test_truncated_prefix_of_history_code_is_invented():
    # GM4101 e' PREFISSO di GM41013: la vecchia guardia a sottostringa lo faceva
    # passare. E' un codice DIVERSO -> invenzione.
    ctx = _ctx(("user", "prezzo del codice GM41013"))
    llm = FakeLLM("prezzo del codice GM4101")
    rq = contextualize_query("e quanto costa?", ctx, llm)
    assert rq.changed is False
    assert rq.reason.startswith("invented-id")
    assert "GM4101" in rq.reason
    assert rq.resolved == "e quanto costa?"


def test_suffix_of_history_code_is_invented():
    # M41013 e' SUFFISSO di GM41013 -> codice diverso, invenzione.
    ctx = _ctx(("user", "prezzo del codice GM41013"))
    llm = FakeLLM("prezzo del codice M41013")
    rq = contextualize_query("e quanto costa?", ctx, llm)
    assert rq.changed is False
    assert rq.reason.startswith("invented-id")
    assert "M41013" in rq.reason
    assert rq.resolved == "e quanto costa?"


def test_numeric_substring_of_history_code_is_invented():
    # 4101 e' sottostringa numerica di GM41013 -> non e' quel codice.
    ctx = _ctx(("user", "prezzo del codice GM41013"))
    llm = FakeLLM("prezzo del codice 4101")
    rq = contextualize_query("e quanto costa?", ctx, llm)
    assert rq.changed is False
    assert rq.reason.startswith("invented-id")
    assert "4101" in rq.reason
    assert rq.resolved == "e quanto costa?"


def test_same_code_with_separator_is_accepted():
    # GM-41013 canonicalizza in GM41013 (identica normalizzazione del lookup
    # SQL a valle): stesso identificatore, NON un'invenzione.
    ctx = _ctx(("user", "prezzo del codice GM41013"))
    llm = FakeLLM("prezzo del codice GM-41013")
    rq = contextualize_query("e quanto costa?", ctx, llm)
    assert rq.changed is True and rq.reason == "resolved"
    assert rq.resolved == "prezzo del codice GM-41013"


def test_same_code_different_case_is_accepted():
    ctx = _ctx(("user", "prezzo del codice GM41013"))
    llm = FakeLLM("prezzo del codice gm41013")
    rq = contextualize_query("e quanto costa?", ctx, llm)
    assert rq.changed is True and rq.reason == "resolved"
    assert rq.resolved == "prezzo del codice gm41013"


def test_completely_new_code_still_discarded():
    # Regressione: il caso classico resta scartato.
    ctx = _ctx(("user", "prezzo del codice GM41013"))
    llm = FakeLLM("prezzo del codice ABC999")
    rq = contextualize_query("e quanto costa?", ctx, llm)
    assert rq.changed is False
    assert rq.reason.startswith("invented-id")
    assert "ABC999" in rq.reason
    assert rq.resolved == "e quanto costa?"


# ------------------------------------------------------------ non-fatale

def test_llm_error_is_non_fatal():
    ctx = _ctx(("user", "prezzo del modello ABC123"))
    llm = FakeLLM(error=RuntimeError("modello giu'"))
    rq = contextualize_query("e per quello nuovo?", ctx, llm)
    assert rq.resolved == "e per quello nuovo?"
    assert rq.changed is False and rq.reason == "llm-error"


# ------------------------------------------------------------ no-op

def test_already_autonomous_query_unchanged():
    ctx = _ctx(("user", "parliamo di valvole"))
    # L'LLM restituisce la query invariata (gia' autonoma / cambio argomento).
    llm = FakeLLM("prezzo del codice ABC123")
    rq = contextualize_query("prezzo del codice ABC123", ctx, llm)
    assert rq.changed is False and rq.reason == "no-op"
    assert rq.resolved == "prezzo del codice ABC123"


def test_empty_llm_output_is_noop():
    ctx = _ctx(("user", "parliamo di valvole"))
    llm = FakeLLM("   ")  # output vuoto/spazi
    rq = contextualize_query("prezzo del codice ABC123", ctx, llm)
    assert rq.changed is False and rq.reason == "no-op"
    assert rq.resolved == "prezzo del codice ABC123"


# ------------------------------------------------------------ tipi/contesto

def test_resolvedquery_influenced_populated_on_resolution():
    ctx = _ctx(("user", "prezzo del modello ABC123"))
    llm = FakeLLM("prezzo del modello ABC123 nuovo")
    rq = contextualize_query("e per quello nuovo?", ctx, llm)
    assert isinstance(rq, ResolvedQuery)
    assert "ABC123" in rq.influenced


def test_assembler_window_keeps_last_n():
    raw = [{"role": "user", "content": f"t{i}"} for i in range(10)]
    ctx = ContextAssembler().build(raw, window=6)
    assert len(ctx.turns) == 6
    assert ctx.turns[0].content == "t4" and ctx.turns[-1].content == "t9"
    assert all(isinstance(t, Turn) for t in ctx.turns)
    assert ctx.is_empty() is False
