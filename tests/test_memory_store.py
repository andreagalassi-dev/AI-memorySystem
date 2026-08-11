"""F.2a-1 — Test di integrazione del MemoryStore (con ROLLBACK).

Provano il PRE-FILTRO RBAC su `public.feedback`. Ogni test avvolge le proprie
INSERT in una transazione che viene SEMPRE annullata (ROLLBACK): non lascia
righe residue nella tabella di produzione. Se il DB non e' raggiungibile, il
test SKIPpa (non fallisce).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.registry.memory_store import MemoryHint, MemoryStore, _parse_evidence_ids


def _get_conn():
    """Apre una connessione o SKIPpa il test se il DB e' irraggiungibile."""
    try:
        from src.backend.clients.postgres_client import get_pg_conn

        return get_pg_conn()
    except Exception as exc:  # DB down / config mancante
        pytest.skip(f"Postgres non raggiungibile: {exc}")


def _insert(conn, **kw) -> int:
    """INSERT diretto di una riga di test (dentro la transazione da annullare)."""
    citazioni = kw.get("citazioni")
    contesto = kw.get("contesto")
    row = conn.execute(
        """
        INSERT INTO public.feedback (
            utente, ruolo, query, risposta, final_level,
            citazioni, contesto, verdetto, livello_memoria, stato_moderazione,
            conferme_count, created_at
        )
        VALUES (
            %(utente)s, %(ruolo)s, %(query)s, %(risposta)s, %(final_level)s,
            %(citazioni)s, %(contesto)s, %(verdetto)s, %(livello_memoria)s,
            %(stato_moderazione)s, %(conferme_count)s,
            COALESCE(%(created_at)s, now())
        )
        RETURNING id
        """,
        {
            "utente": kw["utente"],
            "ruolo": kw["ruolo"],
            "query": kw.get("query", "q"),
            "risposta": kw.get("risposta", "r"),
            "final_level": kw.get("final_level"),
            "citazioni": json.dumps(citazioni) if citazioni is not None else None,
            "contesto": json.dumps(contesto) if contesto is not None else None,
            "verdetto": kw["verdetto"],
            "livello_memoria": kw.get("livello_memoria"),
            "stato_moderazione": kw["stato_moderazione"],
            "conferme_count": kw.get("conferme_count", 0),
            "created_at": kw.get("created_at"),
        },
    ).fetchone()
    return row[0]


def test_recall_rbac_tecnico_alice():
    conn = _get_conn()
    store = MemoryStore()
    try:
        before = conn.execute("SELECT COUNT(*) FROM public.feedback").fetchone()[0]

        # Marco ogni riga con una query univoca per riconoscerla nei risultati.
        markers = {
            "R1": "MARK_R1_personale_alice",
            "R2": "MARK_R2_categoria_appr",
            "R3": "MARK_R3_categoria_pend",
            "R4": "MARK_R4_aziendale_appr",
            "R5": "MARK_R5_rejected",
            "R6": "MARK_R6_correggi",
        }
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=markers["R1"], citazioni=[["docX", 5]])
        _insert(conn, utente="carla", ruolo="tecnico", verdetto="conferma",
                livello_memoria="categoria", stato_moderazione="approved",
                query=markers["R2"])
        _insert(conn, utente="dino", ruolo="tecnico", verdetto="conferma",
                livello_memoria="categoria", stato_moderazione="pending",
                query=markers["R3"])
        _insert(conn, utente="erik", ruolo="amministrazione", verdetto="conferma",
                livello_memoria="aziendale", stato_moderazione="approved",
                query=markers["R4"])
        _insert(conn, utente="frank", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="rejected",
                query=markers["R5"])
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="correggi",
                livello_memoria="personale", stato_moderazione="pending",
                query=markers["R6"])

        hints = store.recall(conn, role="tecnico", utente="alice", limit=1000)
        seen = {h.query for h in hints}

        assert markers["R1"] in seen  # propria personale
        assert markers["R2"] in seen  # categoria-tecnico approvata
        assert markers["R4"] in seen  # aziendale approvata
        assert markers["R3"] not in seen  # categoria pending
        assert markers["R5"] not in seen  # rejected
        assert markers["R6"] not in seen  # non confermata

        # evidence_ids di R1 popolato correttamente da citazioni.
        r1 = next(h for h in hints if h.query == markers["R1"])
        assert r1.evidence_ids == (("docX", 5),)
        assert isinstance(r1, MemoryHint)
    finally:
        conn.rollback()
        conn.close()

    # Verifica esterna che il rollback non abbia lasciato righe.
    conn2 = _get_conn()
    try:
        after = conn2.execute("SELECT COUNT(*) FROM public.feedback").fetchone()[0]
        assert after == before, f"rollback fallito: {before} -> {after}"
    finally:
        conn2.rollback()
        conn2.close()


def test_recall_rbac_commerciale_bob():
    conn = _get_conn()
    store = MemoryStore()
    try:
        markers = {
            "R1": "MARK2_R1_personale_alice",
            "R2": "MARK2_R2_categoria_appr",
            "R3": "MARK2_R3_categoria_pend",
            "R4": "MARK2_R4_aziendale_appr",
            "R5": "MARK2_R5_rejected",
            "R6": "MARK2_R6_correggi",
        }
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=markers["R1"], citazioni=[["docX", 5]])
        _insert(conn, utente="carla", ruolo="tecnico", verdetto="conferma",
                livello_memoria="categoria", stato_moderazione="approved",
                query=markers["R2"])
        _insert(conn, utente="dino", ruolo="tecnico", verdetto="conferma",
                livello_memoria="categoria", stato_moderazione="pending",
                query=markers["R3"])
        _insert(conn, utente="erik", ruolo="amministrazione", verdetto="conferma",
                livello_memoria="aziendale", stato_moderazione="approved",
                query=markers["R4"])
        _insert(conn, utente="frank", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="rejected",
                query=markers["R5"])
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="correggi",
                livello_memoria="personale", stato_moderazione="pending",
                query=markers["R6"])

        hints = store.recall(conn, role="commerciale", utente="bob", limit=1000)
        seen = {h.query for h in hints}

        assert markers["R4"] in seen  # aziendale approvata: visibile a tutti
        assert markers["R1"] not in seen  # personale di alice
        assert markers["R2"] not in seen  # categoria tecnico (bob e' commerciale)
        assert markers["R3"] not in seen  # categoria pending
        assert markers["R5"] not in seen  # rejected
        assert markers["R6"] not in seen  # non confermata
    finally:
        conn.rollback()
        conn.close()


def test_parse_evidence_ids_robusto():
    """Il parser non deve mai sollevare su input null/malformati."""
    assert _parse_evidence_ids(None) == ()
    assert _parse_evidence_ids([]) == ()
    assert _parse_evidence_ids([["docX", 5]]) == (("docX", 5),)
    assert _parse_evidence_ids([{"doc": "docY", "page": 3}]) == (("docY", 3),)
    # elementi malformati saltati, quelli buoni conservati.
    assert _parse_evidence_ids([["ok", 1], "garbage", ["bad"], [1, 2, 3]]) == (
        ("ok", 1),
        ("1", 2),
    )
    assert _parse_evidence_ids("not-a-list") == ()


# =====================================================================
# F.2a-2 — identita'/provenienza dell'hint e pre-filtro testuale
# =====================================================================

def test_recall_returns_hint_id_created_at_and_intent():
    conn = _get_conn()
    store = MemoryStore()
    try:
        marker = "MARK3_pompa_idraulica_con_intent"
        new_id = _insert(conn, utente="alice", ruolo="tecnico",
                         verdetto="conferma", livello_memoria="personale",
                         stato_moderazione="pending", query=marker,
                         contesto={"intent": "structured"})
        hints = store.recall(conn, role="tecnico", utente="alice", limit=1000)
        h = next(x for x in hints if x.query == marker)
        assert h.hint_id == new_id
        assert isinstance(h.created_at, datetime)
        assert h.intent == "structured"
    finally:
        conn.rollback()
        conn.close()


def test_recall_intent_none_on_historical_rows():
    """Le righe STORICHE non hanno 'intent' in `contesto`: NULL, senza errori."""
    conn = _get_conn()
    store = MemoryStore()
    try:
        m_no_key = "MARK3_pompa_idraulica_senza_intent"
        m_null = "MARK3_pompa_idraulica_contesto_null"
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=m_no_key, contesto={"evidence": "x"})
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=m_null, contesto=None)
        hints = store.recall(conn, role="tecnico", utente="alice", limit=1000)
        seen = {h.query: h for h in hints}
        assert seen[m_no_key].intent is None
        assert seen[m_null].intent is None
    finally:
        conn.rollback()
        conn.close()


def test_recall_text_prefilter_narrows_candidates():
    conn = _get_conn()
    store = MemoryStore()
    try:
        pertinente = "MARK3 pompa idraulica del gruppo sterzo"
        scorrelata = "MARK3 cinghia distribuzione del motore"
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=pertinente)
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=scorrelata)

        seen = {h.query for h in store.recall(
            conn, role="tecnico", utente="alice", query="pompa idraulica",
            limit=1000)}
        assert pertinente in seen
        assert scorrelata not in seen

        # Senza `query`: nessun pre-filtro (comportamento F.2a-1).
        seen_all = {h.query for h in store.recall(
            conn, role="tecnico", utente="alice", limit=1000)}
        assert pertinente in seen_all and scorrelata in seen_all

        # F.2a-3, CAMBIO DI COMPORTAMENTO VOLUTO: prima, una domanda senza
        # "termini significativi" disattivava il pre-filtro e restituiva
        # TUTTO. Col full-text non esiste piu' quella nozione: se nessun
        # lessema della domanda compare in nessuna memoria, non si propone
        # nulla. Fail-closed, e piu' onesto: proporre l'intero archivio a chi
        # ha scritto "quanto costa?" e' rumore, non recall.
        seen_stop = {h.query for h in store.recall(
            conn, role="tecnico", utente="alice", query="quanto costa?",
            limit=1000)}
        assert pertinente not in seen_stop and scorrelata not in seen_stop
    finally:
        conn.rollback()
        conn.close()


def test_text_prefilter_does_not_loosen_rbac():
    """Il pre-filtro testuale e' un AND in piu': non puo' aprire nulla."""
    conn = _get_conn()
    store = MemoryStore()
    try:
        markers = {
            "R1": "MARK4 pompa idraulica personale alice",
            "R2": "MARK4 pompa idraulica categoria approvata",
            "R3": "MARK4 pompa idraulica categoria pending",
            "R4": "MARK4 pompa idraulica aziendale approvata",
            "R5": "MARK4 pompa idraulica rifiutata",
            "R6": "MARK4 pompa idraulica da correggere",
        }
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=markers["R1"])
        _insert(conn, utente="carla", ruolo="tecnico", verdetto="conferma",
                livello_memoria="categoria", stato_moderazione="approved",
                query=markers["R2"])
        _insert(conn, utente="dino", ruolo="tecnico", verdetto="conferma",
                livello_memoria="categoria", stato_moderazione="pending",
                query=markers["R3"])
        _insert(conn, utente="erik", ruolo="amministrazione", verdetto="conferma",
                livello_memoria="aziendale", stato_moderazione="approved",
                query=markers["R4"])
        _insert(conn, utente="frank", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="rejected",
                query=markers["R5"])
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="correggi",
                livello_memoria="personale", stato_moderazione="pending",
                query=markers["R6"])

        seen = {h.query for h in store.recall(
            conn, role="commerciale", utente="bob", query="pompa idraulica",
            limit=1000)}
        # Stesse identiche asserzioni di leak del test RBAC esistente.
        assert markers["R4"] in seen       # aziendale approvata
        assert markers["R1"] not in seen   # personale di alice
        assert markers["R2"] not in seen   # categoria tecnico
        assert markers["R3"] not in seen   # categoria pending
        assert markers["R5"] not in seen   # rejected
        assert markers["R6"] not in seen   # non confermata
    finally:
        conn.rollback()
        conn.close()


def test_prefilter_is_parameterized_against_injection():
    """Il termine e' un PARAMETRO: un apostrofo non puo' rompere/aprire nulla."""
    conn = _get_conn()
    store = MemoryStore()
    try:
        marker = "MARK5 pompa idraulica innocua"
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=marker)
        hints = store.recall(
            conn, role="tecnico", utente="alice",
            query="pompa'; DROP TABLE public.feedback; --", limit=1000)
        assert isinstance(hints, list)
        assert conn.execute(
            "SELECT COUNT(*) FROM public.feedback").fetchone()[0] >= 1
    finally:
        conn.rollback()
        conn.close()


# =====================================================================
# F.2a-3 — il pre-filtro/ranking testuale passa al full-text di Postgres
#
# Motivo del cambio: la stoplist IT/EN/DE curata a mano (90 parole importate
# da `src.graph.memory_policy`) e' insostenibile e un tester l'ha gia' bucata.
# Postgres porta stopword E stemming per lingua, mantenuti da altri.
# =====================================================================

def test_recall_fts_matches_inflected_forms():
    """ROSSO con ILIKE: 'pompe idrauliche' non e' sottostringa di 'pompa idraulica'.

    E' il guadagno concreto dello stemming: l'utente scrive al plurale, la
    memoria e' stata salvata al singolare. ILIKE non le lega, il full-text si.
    """
    conn = _get_conn()
    store = MemoryStore()
    try:
        pertinente = "MARK6 pompa idraulica del gruppo sterzo"
        scorrelata = "MARK6 cinghia distribuzione del motore"
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=pertinente)
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=scorrelata)

        seen = {h.query for h in store.recall(
            conn, role="tecnico", utente="alice", query="pompe idrauliche",
            limit=1000)}
        assert pertinente in seen, "lo stemming italiano deve legare pompe->pomp"
        assert scorrelata not in seen
    finally:
        conn.rollback()
        conn.close()


def test_recall_fts_ranks_more_relevant_first():
    """ROSSO con ILIKE: l'ordinamento non conosceva la pertinenza.

    A ha molti lessemi in comune con la domanda, B uno solo; B e' piu'
    RECENTE (created_at esplicito: `now()` e' costante nella transazione) e ha
    lo stesso `conferme_count`. Prima del ranking vinceva B per
    `created_at DESC`: ora deve vincere A.
    """
    conn = _get_conn()
    store = MemoryStore()
    try:
        a = "MARK7 pompa idraulica del gruppo sterzo con valvola"
        b = "MARK7 pompa da giardino"
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=a, conferme_count=1,
                created_at=datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc))
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query=b, conferme_count=1,
                created_at=datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc))

        got = [h.query for h in store.recall(
            conn, role="tecnico", utente="alice",
            query="pompa idraulica gruppo sterzo valvola", limit=1000)]
        assert got.index(a) < got.index(b), got
    finally:
        conn.rollback()
        conn.close()


def test_recall_order_is_deterministic_on_full_ties():
    """ROSSO senza `id DESC`: a parita' di tutto l'ordine e' arbitrario.

    Per una card che l'utente VEDE, due run identici che mostrano suggerimenti
    diversi minano la fiducia piu' di una card imprecisa. Qui rank,
    conferme_count e created_at sono identici per costruzione: resta solo l'id.
    """
    conn = _get_conn()
    store = MemoryStore()
    try:
        ts = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        text = "MARK8 pompa idraulica identica"
        ids = [
            _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                    livello_memoria="personale", stato_moderazione="pending",
                    query=text, conferme_count=3, created_at=ts)
            for _ in range(4)
        ]
        runs = [
            [h.hint_id for h in store.recall(
                conn, role="tecnico", utente="alice",
                query="pompa idraulica identica", limit=1000)
             if h.hint_id in ids]
            for _ in range(5)
        ]
        assert all(r == runs[0] for r in runs), runs
        assert runs[0] == sorted(ids, reverse=True), (runs[0], ids)
    finally:
        conn.rollback()
        conn.close()


def test_recall_deterministic_without_query_too():
    """Stesso determinismo anche con `query=None` (nessun filtro testuale)."""
    conn = _get_conn()
    store = MemoryStore()
    try:
        ts = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)
        ids = [
            _insert(conn, utente="zorro_det", ruolo="tecnico",
                    verdetto="conferma", livello_memoria="personale",
                    stato_moderazione="pending", query="MARK9 qualunque cosa",
                    conferme_count=2, created_at=ts)
            for _ in range(4)
        ]
        runs = [
            [h.hint_id for h in store.recall(
                conn, role="tecnico", utente="zorro_det", limit=1000)]
            for _ in range(5)
        ]
        assert all(r == runs[0] for r in runs), runs
        assert runs[0] == sorted(ids, reverse=True)
    finally:
        conn.rollback()
        conn.close()


def test_recall_no_longer_imports_the_hand_curated_stoplist():
    """La dipendenza `registry -> graph` deve sparire (controllo sull'AST)."""
    import ast
    from pathlib import Path

    import src.registry.memory_store as mod

    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [f"{node.module or ''}.{a.name}" for a in node.names]
    assert [m for m in imported if "memory_policy" in m or m.startswith(
        "src.graph")] == []
    assert not hasattr(mod, "_text_prefilter")
    assert not hasattr(mod, "significant_terms")


@pytest.mark.parametrize("hostile", [
    "l'albero d'acciaio 'quoted'",
    'la "pompa" e il "motore"',
    "100% & 50_ di sconto",
    "%_%",
    "pompa & !motore | (valvola)",
    "pomp:* & idraul:*",
    "'''\\\\ !!! ::: &&& |||",
    "     ",
    "e il la di che",
    "🔧🛠️ la pompa 🚜",
    "pompa idraulica " * 313,
    "pompa hydraulic Pumpe Ersatzteil spare part ricambio",
    "pompa\ttab\nnewline",
    "pompa'; DROP TABLE public.feedback; --",
])
def test_recall_never_raises_on_hostile_query(hostile):
    """Il filtro `&&` non fa PARSING di una tsquery: non puo' sollevare.

    E' la ragione della scelta contro `to_tsquery`, che su
    `'''\\\\ !!! ::: &&& |||` solleva SyntaxError (verificato sul DB reale).
    """
    conn = _get_conn()
    store = MemoryStore()
    try:
        _insert(conn, utente="alice", ruolo="tecnico", verdetto="conferma",
                livello_memoria="personale", stato_moderazione="pending",
                query="MARKA pompa idraulica innocua")
        hints = store.recall(conn, role="tecnico", utente="alice",
                             query=hostile, limit=1000)
        assert isinstance(hints, list)
        # la connessione e' ancora usabile: nessuna transazione abortita.
        assert conn.execute("SELECT 1").fetchone()[0] == 1
    finally:
        conn.rollback()
        conn.close()
