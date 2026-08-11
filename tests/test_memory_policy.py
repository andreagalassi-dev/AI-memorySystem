"""F.2a-2 — Test della POLITICA di autorizzazione della memoria (DB-free, no LLM).

`memory_authorized_ids` e' il cuore di sicurezza del punto: decide SE e QUALE
identificatore la memoria long-term puo' proporre alla riformulazione. Sbagliare
qui significa iniettare nella query un codice REALE ma di un pezzo SBAGLIATO —
il ramo STRUCTURED lo troverebbe a catalogo e risponderebbe ALTA. Il fail-closed
a valle non lo prenderebbe: il dato esiste.

I test sono per lo piu' AVVERSARI: provano che la memoria TACE quando deve.
Gli hint sono costruiti a mano (`MemoryHint`), senza DB.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.graph.context import Turn
from src.graph.memory_policy import (
    STOPWORDS,
    MemoryAuthorization,
    memory_authorized_ids,
    significant_terms,
)
from src.registry.memory_store import MemoryHint


def _hint(query: str, *, hint_id: int = 1, risposta: str | None = None,
          conferme: int = 1) -> MemoryHint:
    return MemoryHint(
        query=query,
        evidence_ids=(),
        conferme_count=conferme,
        livello="personale",
        risposta=risposta,
        hint_id=hint_id,
        created_at=datetime(2026, 1, 15, 9, 30, tzinfo=timezone.utc),
        intent="structured",
    )


def _turns(*pairs: tuple[str, str]) -> tuple[Turn, ...]:
    return tuple(Turn(role=r, content=c) for r, c in pairs)


# ---------------------------------------------------------------- (1) FRATELLI

def test_dialog_with_identifier_blocks_memory_siblings():
    """Il caso piu' pericoloso: prodotti FRATELLI.

    Il dialogo nomina ABC123; un hint molto simile (testi quasi identici,
    proprio perche' i pezzi sono confondibili) porta ABC124. La memoria NON
    deve poter parlare: il referente e' gia' determinato dall'utente.
    """
    turns = _turns(("user", "parlami del modello X, codice ABC123"))
    hints = (_hint("modello Y serie X pompa ABC124", hint_id=7),)
    auth = memory_authorized_ids("e quanto costa?", turns, hints)
    assert auth.identifier is None
    assert auth.hint_id is None
    assert auth.reason == "mem-skip:dialog-has-id"


def test_identifier_in_current_question_blocks_memory():
    """Anche se l'ID e' solo nella DOMANDA corrente (storia pulita)."""
    hints = (_hint("modello Y serie X pompa ABC124", hint_id=7),)
    auth = memory_authorized_ids("quanto costa la pompa ABC123 della serie X?",
                                 (), hints)
    assert auth.identifier is None
    assert auth.reason == "mem-skip:dialog-has-id"


# ------------------------------------------------------------ (2) CASO LEGITTIMO

def test_legitimate_authorization():
    hints = (_hint("modello Y serie X pompa ABC124", hint_id=7),)
    auth = memory_authorized_ids("quanto costa la pompa della serie X?", (), hints)
    assert isinstance(auth, MemoryAuthorization)
    assert auth.identifier == "ABC124"
    assert auth.hint_id == 7
    assert auth.hint is hints[0]
    assert auth.reason == "mem-authorized-id:ABC124@hint#7"


def test_authorized_identifier_is_canonicalized():
    """L'id autorizzato e' CANONICALIZZATO (stessa normalizzazione del SQL)."""
    hints = (_hint("pompa idraulica del gruppo ABC-124", hint_id=3),)
    auth = memory_authorized_ids("quanto costa la pompa idraulica?", (), hints)
    assert auth.identifier == "ABC124"


# ---------------------------------------------------------------- (3) AMBIGUITA'

def test_ambiguous_top_scores_abort():
    hints = (
        _hint("pompa idraulica ABC124", hint_id=1),
        _hint("pompa idraulica ABC125", hint_id=2),
    )
    auth = memory_authorized_ids("quanto costa la pompa idraulica?", (), hints)
    assert auth.identifier is None
    assert auth.hint_id is None
    assert auth.reason == "mem-abort:ambiguous"


# --------------------------------------------------- (4) PARI-MERITO, STESSO ID

def test_tie_with_same_identifier_is_not_ambiguous():
    hints = (
        _hint("pompa idraulica ABC124", hint_id=1),
        _hint("pompa idraulica abc-124", hint_id=2),
    )
    auth = memory_authorized_ids("quanto costa la pompa idraulica?", (), hints)
    assert auth.identifier == "ABC124"
    assert auth.hint_id == 1  # il primo dei pari-merito (ordine dello store)


# ------------------------------------------------------------------- (5) ORIGINE

def test_identifier_from_risposta_is_never_authorized():
    """`risposta` e' prosa LLM confermata NEL COMPLESSO: non e' un binding."""
    hints = (_hint("quanto costa la pompa idraulica",
                   risposta="il codice ABC999 costa 12 EUR", hint_id=4),)
    auth = memory_authorized_ids("quanto costa la pompa idraulica?", (), hints)
    assert auth.identifier is None
    assert "ABC999" not in (auth.reason or "")


# ------------------------------------------------------------------ (6) STOPWORD

def test_stopword_only_overlap_does_not_authorize():
    """L'unica sovrapposizione e' 'prezzo' (stopword) -> score 0."""
    hints = (_hint("prezzo del radiatore ABC124", hint_id=5),)
    auth = memory_authorized_ids("prezzo del carburatore?", (), hints)
    assert auth.identifier is None


def test_single_significant_overlap_is_below_threshold():
    """Un solo termine significativo in comune non basta (soglia >= 2)."""
    hints = (_hint("prezzo del radiatore maggiorato ABC124", hint_id=5),)
    auth = memory_authorized_ids("prezzo del radiatore?", (), hints)
    assert auth.identifier is None


# ------------------------------------------------------- (7) DUE ID NELLA QUERY

def test_hint_with_two_identifiers_authorizes_nothing():
    hints = (_hint("pompa idraulica ABC124 oppure ABC125", hint_id=6),)
    auth = memory_authorized_ids("quanto costa la pompa idraulica?", (), hints)
    assert auth.identifier is None


def test_hint_with_zero_identifiers_authorizes_nothing():
    hints = (_hint("pompa idraulica del gruppo sterzo", hint_id=6),)
    auth = memory_authorized_ids("quanto costa la pompa idraulica?", (), hints)
    assert auth.identifier is None


# ------------------------------------------------------------- (9) NESSUN CRASH

def test_empty_inputs_never_raise():
    auth = memory_authorized_ids("", (), ())
    assert auth.identifier is None and auth.reason == "mem-none"
    assert memory_authorized_ids("qualcosa", (), ()).reason == "mem-none"
    assert memory_authorized_ids("", (), (_hint("pompa ABC124"),)).reason == "mem-none"


def test_none_inputs_never_raise():
    auth = memory_authorized_ids(None, None, None)  # type: ignore[arg-type]
    assert auth.identifier is None and auth.reason == "mem-none"


# --------------------------------------------------------- termini significativi

def test_significant_terms_filters_short_stopwords_and_ids():
    terms = significant_terms("quanto costa la pompa idraulica ABC124?")
    assert "pompa" in terms and "idraulica" in terms
    assert "quanto" not in terms and "costa" not in terms  # stopword
    assert "la" not in terms  # len < 4
    assert "abc124" not in terms and "ABC124" not in terms  # identificatore


def test_stopwords_cover_three_languages():
    for w in ("prezzo", "codice", "price", "code", "preis", "artikel"):
        assert w in STOPWORDS
