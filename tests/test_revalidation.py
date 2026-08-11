"""F.2b — Test del `RevalidationGate`.

Principio: "la memoria PROPONE, l'evidenza viva DISPONE". Nessun hint puo'
essere mostrato all'utente senza che l'evidenza che cita sia stata
ri-verificata VIVA, adesso, per l'utente CORRENTE.

Due livelli:
  - unit, con conn/qdrant FINTI, per le regole e per il conteggio dei
    round-trip (deterministici, sempre eseguibili);
  - integrazione con ROLLBACK su Postgres reale + Qdrant reale, per il test
    piu' importante dell'epica (il leak RBAC). Se DB o Qdrant mancano, SKIP.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone

import pytest

from src.common.roles import Role
from src.registry.memory_store import MemoryHint, MemoryStore
from src.registry.revalidation import (
    DroppedHint,
    RevalidationGate,
    recall_validated,
)

UTC = timezone.utc
FRESH = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)     # conferma recente
OLD_INGEST = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)  # ingestione precedente
NEW_INGEST = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)  # re-ingest DOPO la conferma


# ---------------------------------------------------------------- fakes ----

class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class FakeConn:
    """Finto `psycopg.Connection`: serve solo `document_versions`."""

    def __init__(self, versions: dict[str, tuple[str, datetime]], *,
                 delay_s: float = 0.0, raises: Exception | None = None):
        self.versions = versions
        self.delay_s = delay_s
        self.raises = raises
        self.queries: list[tuple[str, dict]] = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params or {}))
        if self.raises is not None:
            raise self.raises
        if self.delay_s:
            time.sleep(self.delay_s)
        stems = set(params["stems"])
        rows = [(s, st, ts) for s, (st, ts) in self.versions.items()
                if s in stems]
        return _FakeCursor(sorted(rows))


class _Hit:
    def __init__(self, value, count):
        self.value = value
        self.count = count


class _FacetResponse:
    def __init__(self, hits):
        self.hits = hits


class FakeQdrant:
    """Finto client Qdrant che ONORA il filtro RBAC che riceve.

    Cosi' il test non si limita a contare le chiamate: verifica che il gate
    costruisca il filtro giusto (chiave `doc`, ruolo, version_status).
    """

    def __init__(self, points: dict[str, tuple[list[str], str]], *,
                 raises: Exception | None = None, delay_s: float = 0.0):
        # points: doc -> (ruoli_consentiti, version_status)
        self.points = points
        self.raises = raises
        self.delay_s = delay_s
        self.calls: list[dict] = []

    def facet(self, collection_name, *, key, facet_filter, limit,
              exact=False, **kw):
        self.calls.append({"collection": collection_name, "key": key,
                           "limit": limit, "exact": exact,
                           "filter": facet_filter})
        if self.raises is not None:
            raise self.raises
        if self.delay_s:
            time.sleep(self.delay_s)
        role = version_status = None
        asked: list[str] = []
        for cond in facet_filter.must or []:
            if cond.key == "ruoli_consentiti":
                role = cond.match.value
            elif cond.key == "version_status":
                version_status = cond.match.value
            elif cond.key == "doc":
                asked = list(cond.match.any)
        assert key == "doc", "il gate DEVE filtrare su `doc`, non su `doc_stem`"
        hits = [
            _Hit(d, 7) for d in asked
            if d in self.points
            and role in self.points[d][0]
            and version_status == self.points[d][1]
        ]
        return _FacetResponse(hits)


def _hint(query="q", evidence=(("docA", 1),), created_at=FRESH, hint_id=1,
          risposta="testo del binding"):
    return MemoryHint(query=query, evidence_ids=tuple(evidence),
                      conferme_count=1, livello="aziendale", risposta=risposta,
                      hint_id=hint_id, created_at=created_at)


def _ok_world(*stems):
    conn = FakeConn({s: ("current", OLD_INGEST) for s in stems})
    qc = FakeQdrant({s: (["tecnico"], "current") for s in stems})
    return conn, qc


def _reasons(dropped: list[DroppedHint]) -> list[str]:
    return [d.reason for d in dropped]


# ------------------------------------------------------------- caso base ---

def test_happy_path_hint_survives():
    conn, qc = _ok_world("docA")
    kept, dropped = RevalidationGate().validate(
        [_hint()], conn=conn, qc=qc, role=Role.TECNICO)
    assert [h.hint_id for h in kept] == [1]
    assert dropped == []


def test_role_must_be_a_Role_not_a_str():
    """Parita' con `visual_search` (pixelrag/search.py:54): mai un default."""
    conn, qc = _ok_world("docA")
    with pytest.raises(TypeError):
        RevalidationGate().validate([_hint()], conn=conn, qc=qc,
                                    role="tecnico")
    with pytest.raises(TypeError):
        RevalidationGate().validate([_hint()], conn=conn, qc=qc, role=None)


def test_role_type_error_is_raised_before_any_round_trip():
    conn, qc = _ok_world("docA")
    with pytest.raises(TypeError):
        RevalidationGate().validate([_hint()], conn=conn, qc=qc, role="tecnico")
    assert conn.queries == [] and qc.calls == []


# ---------------------------------------------------------- regole di drop --

def test_hint_without_evidence_is_dropped():
    conn, qc = _ok_world("docA")
    kept, dropped = RevalidationGate().validate(
        [_hint(evidence=())], conn=conn, qc=qc, role=Role.TECNICO)
    assert kept == [] and _reasons(dropped) == ["no-evidence"]


def test_hint_without_created_at_is_dropped():
    """Senza data non si puo' PROVARE la freschezza: si scarta."""
    conn, qc = _ok_world("docA")
    kept, dropped = RevalidationGate().validate(
        [_hint(created_at=None)], conn=conn, qc=qc, role=Role.TECNICO)
    assert kept == [] and _reasons(dropped) == ["unknown-age"]


def test_document_absent_from_document_versions_is_dropped():
    conn = FakeConn({})
    qc = FakeQdrant({"docA": (["tecnico"], "current")})
    kept, dropped = RevalidationGate().validate(
        [_hint()], conn=conn, qc=qc, role=Role.TECNICO)
    assert kept == [] and _reasons(dropped) == ["doc-unknown"]


def test_superseded_document_is_dropped():
    conn = FakeConn({"docA": ("superseded", OLD_INGEST)})
    qc = FakeQdrant({"docA": (["tecnico"], "current")})
    kept, dropped = RevalidationGate().validate(
        [_hint()], conn=conn, qc=qc, role=Role.TECNICO)
    assert kept == [] and _reasons(dropped) == ["doc-not-current"]


def test_stale_binding_is_dropped_even_though_the_points_still_exist():
    """IL test della staleness: i punti Qdrant ESISTONO ancora, e non basta.

    `chunk_id` e' POSIZIONALE e `point_id = uuid5(chunk_id)`: un re-ingest
    sovrascrive il CONTENUTO sotto un id stabile. "il chunk esiste ancora" non
    prova NIENTE sul fatto che dica ancora la stessa cosa. L'unica prova
    onesta disponibile e' `ingested_at` vs `created_at`.
    """
    conn = FakeConn({"docA": ("current", NEW_INGEST)})   # re-ingest DOPO
    qc = FakeQdrant({"docA": (["tecnico"], "current")})  # punti ANCORA presenti
    kept, dropped = RevalidationGate().validate(
        [_hint(created_at=FRESH)], conn=conn, qc=qc, role=Role.TECNICO)
    assert kept == [], "un binding piu' vecchio dell'ultima ingestione non e' verificabile"
    assert _reasons(dropped) == ["stale-binding"]

    # controprova: stessa identica situazione con ingestione PRECEDENTE.
    conn2 = FakeConn({"docA": ("current", OLD_INGEST)})
    kept2, _ = RevalidationGate().validate(
        [_hint(created_at=FRESH)], conn=conn2, qc=qc, role=Role.TECNICO)
    assert [h.hint_id for h in kept2] == [1]


def test_stem_not_visible_to_the_session_role_is_dropped():
    conn = FakeConn({"docA": ("current", OLD_INGEST)})
    qc = FakeQdrant({"docA": (["tecnico"], "current")})
    kept, dropped = RevalidationGate().validate(
        [_hint()], conn=conn, qc=qc, role=Role.COMMERCIALE)
    assert kept == [] and _reasons(dropped) == ["rbac-or-missing"]


def test_all_stems_must_pass_no_partial_validation():
    """3 citazioni, una sola invisibile => l'INTERO hint cade (AND)."""
    conn = FakeConn({s: ("current", OLD_INGEST) for s in ("d1", "d2", "d3")})
    qc = FakeQdrant({"d1": (["tecnico"], "current"),
                     "d2": (["tecnico"], "current"),
                     "d3": (["amministrazione"], "current")})
    h = _hint(evidence=(("d1", 1), ("d2", 2), ("d3", 3)))
    kept, dropped = RevalidationGate().validate(
        [h], conn=conn, qc=qc, role=Role.TECNICO)
    assert kept == [] and _reasons(dropped) == ["rbac-or-missing"]


# ---------------------------------------------------------- fail-safe -------

def test_qdrant_exception_drops_everything_without_propagating():
    conn = FakeConn({"docA": ("current", OLD_INGEST)})
    qc = FakeQdrant({}, raises=RuntimeError("qdrant giu'"))
    kept, dropped = RevalidationGate().validate(
        [_hint(hint_id=1), _hint(hint_id=2)], conn=conn, qc=qc,
        role=Role.TECNICO)
    assert kept == []
    assert _reasons(dropped) == ["gate-unavailable"] * 2


def test_postgres_exception_drops_everything_without_propagating():
    conn = FakeConn({}, raises=RuntimeError("db giu'"))
    qc = FakeQdrant({"docA": (["tecnico"], "current")})
    kept, dropped = RevalidationGate().validate(
        [_hint()], conn=conn, qc=qc, role=Role.TECNICO)
    assert kept == [] and _reasons(dropped) == ["gate-unavailable"]


def test_budget_exceeded_drops_everything():
    conn = FakeConn({"docA": ("current", OLD_INGEST)}, delay_s=0.10)
    qc = FakeQdrant({"docA": (["tecnico"], "current")})
    kept, dropped = RevalidationGate().validate(
        [_hint()], conn=conn, qc=qc, role=Role.TECNICO, budget_s=0.01)
    assert kept == [] and _reasons(dropped) == ["gate-unavailable"]


def test_gate_unavailable_covers_even_the_hints_already_rejected():
    """Fail-closed totale: se il gate non sa decidere, non passa NULLA."""
    conn = FakeConn({}, raises=RuntimeError("db giu'"))
    qc = FakeQdrant({})
    kept, dropped = RevalidationGate().validate(
        [_hint(hint_id=1), _hint(hint_id=2, evidence=())],
        conn=conn, qc=qc, role=Role.TECNICO)
    assert kept == []
    assert _reasons(dropped) == ["gate-unavailable"] * 2


def test_empty_input_does_no_round_trip():
    conn, qc = _ok_world("docA")
    assert RevalidationGate().validate([], conn=conn, qc=qc,
                                       role=Role.TECNICO) == ([], [])
    assert conn.queries == [] and qc.calls == []


def test_no_evidence_only_input_does_no_round_trip():
    conn, qc = _ok_world("docA")
    kept, dropped = RevalidationGate().validate(
        [_hint(evidence=())], conn=conn, qc=qc, role=Role.TECNICO)
    assert kept == [] and _reasons(dropped) == ["no-evidence"]
    assert conn.queries == [] and qc.calls == []


# --------------------------------------------------------- round-trip ------

def test_round_trips_are_one_sql_and_one_facet():
    """5 hint su 5 stem distinti => 1 SQL + 1 facet. O(1), non O(hint x stem)."""
    stems = [f"doc{i}" for i in range(5)]
    conn, qc = _ok_world(*stems)
    hints = [_hint(evidence=((s, 1),), hint_id=i)
             for i, s in enumerate(stems)]
    kept, dropped = RevalidationGate().validate(
        hints, conn=conn, qc=qc, role=Role.TECNICO)
    assert len(kept) == 5 and dropped == []
    assert len(conn.queries) == 1, conn.queries
    assert len(qc.calls) == 1, qc.calls
    # gli stem sono DEDUPLICATI e ordinati, e il limit copre tutti i valori
    # possibili (il MatchAny li limita gia': nessun troncamento silenzioso).
    assert sorted(conn.queries[0][1]["stems"]) == sorted(stems)
    assert qc.calls[0]["limit"] >= len(stems)


def test_round_trips_stay_two_with_shared_and_repeated_stems():
    conn, qc = _ok_world("d1", "d2")
    hints = [_hint(evidence=(("d1", 1), ("d2", 2)), hint_id=i)
             for i in range(5)]
    RevalidationGate().validate(hints, conn=conn, qc=qc, role=Role.TECNICO)
    assert len(conn.queries) == 1 and len(qc.calls) == 1
    assert conn.queries[0][1]["stems"] == ["d1", "d2"]


def test_gate_never_reruns_the_righe_listino_lookup():
    """La memoria non serve VALORI: nessuna query al listino (AST)."""
    import ast
    from pathlib import Path

    import src.registry.revalidation as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    # I docstring PARLANO di righe_listino (e' li' che la scelta e'
    # motivata): il controllo deve guardare il CODICE, non la prosa.
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    literals = [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and id(n) not in docstrings and "select" in n.value.lower()]
    assert literals, "atteso almeno un SELECT nel modulo"
    for sql in literals:
        assert "righe_listino" not in sql.lower(), sql
        assert "estrazione." not in sql.lower(), sql


# ------------------------------------------- integrazione (DB + Qdrant) -----

def _get_conn():
    try:
        from src.backend.clients.postgres_client import get_pg_conn

        return get_pg_conn()
    except Exception as exc:
        pytest.skip(f"Postgres non raggiungibile: {exc}")


def _get_qc():
    try:
        from src.backend.clients.qdrant_client import get_qdrant_client

        qc = get_qdrant_client()
        qc.get_collections()
        return qc
    except Exception as exc:
        pytest.skip(f"Qdrant non raggiungibile: {exc}")


# Documenti REALI del corpus, verificati sul Qdrant di produzione:
#   flow_exploded_2022            -> ruoli_consentiti ['tecnico'], 21 punti
#   pi_pulley_listino_pulegge_2025 -> visibile a commerciale, 1147 punti
# Entrambi 'current' in document_versions, ingested_at 2026-07-25.
DOC_SOLO_TECNICO = "flow_exploded_2022"
DOC_ANCHE_COMMERCIALE = "pi_pulley_listino_pulegge_2025"
SEGRETO = "SEGRETO_TESTO_DEL_BINDING_CHE_NON_DEVE_APPARIRE"


def _insert_feedback(conn, **kw) -> int:
    return conn.execute(
        """
        INSERT INTO public.feedback (
            utente, ruolo, query, risposta, citazioni, verdetto,
            livello_memoria, stato_moderazione, conferme_count, created_at)
        VALUES (%(utente)s, %(ruolo)s, %(query)s, %(risposta)s, %(citazioni)s,
                'conferma', %(livello_memoria)s, %(stato_moderazione)s, 3,
                %(created_at)s)
        RETURNING id
        """,
        {
            "utente": kw["utente"], "ruolo": kw["ruolo"],
            "query": kw["query"], "risposta": kw.get("risposta", SEGRETO),
            "citazioni": json.dumps(kw["citazioni"]),
            "livello_memoria": kw.get("livello_memoria", "aziendale"),
            "stato_moderazione": kw.get("stato_moderazione", "approved"),
            "created_at": kw.get("created_at",
                                 datetime.now(UTC) + timedelta(seconds=0)),
        },
    ).fetchone()[0]


def test_INTEGRAZIONE_binding_aziendale_su_documento_tecnico_non_raggiunge_il_commerciale():
    """IL test dell'epica: il pre-filtro NON basta, il gate SI.

    Un binding `aziendale`+`approved` e' visibile a chiunque per il pre-filtro
    di `memory_store` — che filtra la RIGA DI FEEDBACK, non l'EVIDENZA. Se cita
    un documento riservato ai tecnici, il suo testo finirebbe sullo schermo di
    un commerciale. Questo gate e' l'unica cosa che lo impedisce.
    """
    conn = _get_conn()
    qc = _get_qc()
    try:
        before = conn.execute(
            "SELECT COUNT(*) FROM public.feedback").fetchone()[0]
        marker = "MARKRBAC pompa idraulica del flow exploded"
        _insert_feedback(
            conn, utente="mallory", ruolo="tecnico", query=marker,
            citazioni=[{"doc": DOC_SOLO_TECNICO, "page": 7}])

        # 1) il pre-filtro di memory_store LO RESTITUISCE al commerciale.
        raw = MemoryStore().recall(conn, role="commerciale", utente="bob",
                                   query="pompa idraulica flow exploded",
                                   limit=50)
        assert marker in {h.query for h in raw}, (
            "premessa del test: il pre-filtro deve lasciarlo passare")

        # 2) il gate lo scarta: il ruolo di SESSIONE non vede quel documento.
        kept = recall_validated(conn, qc, role=Role.COMMERCIALE, utente="bob",
                                query="pompa idraulica flow exploded", limit=50)
        assert kept == []
        blob = repr(kept)
        assert SEGRETO not in blob and marker not in blob

        # 3) controprova: allo stesso binding, un TECNICO arriva.
        kept_t = recall_validated(conn, qc, role=Role.TECNICO, utente="bob",
                                  query="pompa idraulica flow exploded",
                                  limit=50)
        assert marker in {h.query for h in kept_t}
    finally:
        conn.rollback()
        conn.close()

    conn2 = _get_conn()
    try:
        after = conn2.execute(
            "SELECT COUNT(*) FROM public.feedback").fetchone()[0]
        assert after == before, f"rollback fallito: {before} -> {after}"
    finally:
        conn2.rollback()
        conn2.close()


def test_INTEGRAZIONE_il_ruolo_di_sessione_batte_feedback_ruolo():
    """`feedback.ruolo='tecnico'` + sessione `commerciale` => decide la sessione.

    Nessuna ereditarieta' di privilegio: il ruolo di chi CONFERMO' il binding
    non deve mai autorizzare chi lo LEGGE oggi.
    """
    conn = _get_conn()
    qc = _get_qc()
    try:
        marker = "MARKSESS pompa idraulica del flow exploded"
        _insert_feedback(conn, utente="bob", ruolo="tecnico", query=marker,
                         livello_memoria="personale",
                         stato_moderazione="pending",
                         citazioni=[{"doc": DOC_SOLO_TECNICO, "page": 7}])
        # stessa riga, stesso utente: cambia SOLO il ruolo di sessione.
        as_comm = recall_validated(conn, qc, role=Role.COMMERCIALE,
                                   utente="bob", query="pompa idraulica flow",
                                   limit=50)
        as_tech = recall_validated(conn, qc, role=Role.TECNICO, utente="bob",
                                   query="pompa idraulica flow", limit=50)
        assert marker not in {h.query for h in as_comm}
        assert marker in {h.query for h in as_tech}
    finally:
        conn.rollback()
        conn.close()


def test_MemoryHint_non_trasporta_il_ruolo_di_chi_confermo():
    """Prova STRUTTURALE: il gate non puo' nemmeno leggere `feedback.ruolo`."""
    assert "ruolo" not in MemoryHint.__dataclass_fields__


def test_INTEGRAZIONE_caso_felice_su_documento_visibile_al_commerciale():
    conn = _get_conn()
    qc = _get_qc()
    try:
        marker = "MARKOK listino pulegge per il commerciale"
        _insert_feedback(conn, utente="bob", ruolo="commerciale", query=marker,
                         citazioni=[{"doc": DOC_ANCHE_COMMERCIALE, "page": 3}])
        kept = recall_validated(conn, qc, role=Role.COMMERCIALE, utente="bob",
                                query="listino pulegge", limit=50)
        assert marker in {h.query for h in kept}
    finally:
        conn.rollback()
        conn.close()


def test_INTEGRAZIONE_documento_inesistente_scartato_anche_col_ruolo_giusto():
    conn = _get_conn()
    qc = _get_qc()
    try:
        marker = "MARKGHOST pompa idraulica di un documento fantasma"
        _insert_feedback(conn, utente="bob", ruolo="tecnico", query=marker,
                         citazioni=[{"doc": "documento_mai_esistito_zzz",
                                     "page": 1}])
        kept = recall_validated(conn, qc, role=Role.TECNICO, utente="bob",
                                query="pompa idraulica documento fantasma",
                                limit=50)
        assert kept == []
    finally:
        conn.rollback()
        conn.close()


def test_INTEGRAZIONE_binding_piu_vecchio_dellingestione_e_scartato():
    """`created_at` PRIMA di `ingested_at` (2026-07-25) => stale, anche se
    documento current e ruolo giusto."""
    conn = _get_conn()
    qc = _get_qc()
    try:
        marker = "MARKSTALE pompa idraulica del flow exploded vecchia"
        _insert_feedback(
            conn, utente="bob", ruolo="tecnico", query=marker,
            citazioni=[{"doc": DOC_SOLO_TECNICO, "page": 7}],
            created_at=datetime(2026, 1, 1, tzinfo=UTC))
        kept = recall_validated(conn, qc, role=Role.TECNICO, utente="bob",
                                query="pompa idraulica flow exploded vecchia",
                                limit=50)
        assert kept == []
    finally:
        conn.rollback()
        conn.close()


def test_INTEGRAZIONE_facet_su_doc_stem_darebbe_zero_la_chiave_giusta_e_doc():
    """Controprova sul Qdrant REALE: `doc_stem` e' assente da molti punti.

    Un gate che filtrasse su `doc_stem` scarterebbe TUTTO. Qui si prova che
    la chiave usata dal gate (`doc`) trova i punti e `doc_stem` no.
    """
    from qdrant_client import models

    from src.retrieval.search import COLLECTION, access_filter

    qc = _get_qc()
    base = access_filter("current", Role.TECNICO)
    for key, expected_non_vuoto in (("doc", True), ("doc_stem", False)):
        flt = base.model_copy(update={"must": list(base.must) + [
            models.FieldCondition(
                key=key,
                match=models.MatchAny(any=[DOC_SOLO_TECNICO]))]})
        hits = qc.facet(COLLECTION, key="doc", facet_filter=flt, limit=5,
                        exact=True).hits
        assert bool(hits) is expected_non_vuoto, (key, hits)


def test_recall_validated_role_must_be_a_Role():
    conn = FakeConn({})
    qc = FakeQdrant({})
    with pytest.raises(TypeError):
        recall_validated(conn, qc, role="tecnico", utente="bob")
