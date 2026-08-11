"""F.2a-1 — Test di integrazione del MemoryStore (con ROLLBACK).

Provano il PRE-FILTRO RBAC su `public.feedback`. Ogni test avvolge le proprie
INSERT in una transazione che viene SEMPRE annullata (ROLLBACK): non lascia
righe residue nella tabella di produzione. Se il DB non e' raggiungibile, il
test SKIPpa (non fallisce).
"""

from __future__ import annotations

import json

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
    row = conn.execute(
        """
        INSERT INTO public.feedback (
            utente, ruolo, query, risposta, final_level,
            citazioni, verdetto, livello_memoria, stato_moderazione
        )
        VALUES (
            %(utente)s, %(ruolo)s, %(query)s, %(risposta)s, %(final_level)s,
            %(citazioni)s, %(verdetto)s, %(livello_memoria)s, %(stato_moderazione)s
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
            "verdetto": kw["verdetto"],
            "livello_memoria": kw.get("livello_memoria"),
            "stato_moderazione": kw["stato_moderazione"],
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
