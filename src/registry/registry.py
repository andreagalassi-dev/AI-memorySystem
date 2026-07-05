"""P4.1 — Accesso al registro versioni (SPEC §E). Connessione INIETTATA.

Postgres e' la fonte di verita' di versioni e ruoli; Qdrant ne e' la
proiezione (backfill in P4.2). Ogni funzione di scrittura e' un'unita'
transazionale (commit interno); le letture non toccano la transazione.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import psycopg

from src.common.roles import Role, parse_role

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

CURRENT = "current"
SUPERSEDED = "superseded"
DELETED = "deleted"


class RegisterResult(NamedTuple):
    document_id: int
    version_id: int
    created: bool


class VersionRecord(NamedTuple):
    version_id: int
    document_id: int
    doc_stem: str
    version_no: int
    version_status: str
    langs: list[str]
    superseded_at: datetime | None


class BackfillRecord(NamedTuple):
    version_status: str
    doc_version: int
    ruoli: list[Role]


class DocumentRecord(NamedTuple):
    document_id: int
    slug: str
    category: str | None
    current_stem: str | None


_VERSION_COLS = ("v.id, v.document_id, v.doc_stem, v.version_no, "
                 "v.version_status, v.langs, v.superseded_at")


def init_schema(conn: psycopg.Connection) -> None:
    """Applica il DDL (idempotente: IF NOT EXISTS ovunque)."""
    conn.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def register_document(conn: psycopg.Connection, *, slug: str, category: str | None,
                      roles: list[Role], doc_stem: str, langs: list[str],
                      version_no: int = 1, title: str | None = None) -> RegisterResult:
    """Upsert idempotente: documento logico + versione fisica + ruoli.

    Se doc_stem e' gia' registrato non modifica nulla (created=False).
    """
    roles = [parse_role(r) for r in roles]  # fail-closed su ruoli non validi
    if not roles:
        raise ValueError(f"documento {slug!r} senza ruoli: il filtro RBAC sarebbe vuoto")

    existing = get_version_by_stem(conn, doc_stem)
    if existing is not None:
        got = conn.execute("SELECT slug FROM documents WHERE id = %s",
                           (existing.document_id,)).fetchone()[0]
        if got != slug:
            raise ValueError(f"doc_stem {doc_stem!r} gia' registrato sotto {got!r}, "
                             f"non {slug!r}")
        return RegisterResult(existing.document_id, existing.version_id, created=False)

    doc_id = conn.execute(
        "INSERT INTO documents (slug, title, category) VALUES (%s, %s, %s) "
        "ON CONFLICT (slug) DO UPDATE SET slug = EXCLUDED.slug RETURNING id",
        (slug, title, category)).fetchone()[0]
    ver_id = conn.execute(
        "INSERT INTO document_versions "
        "(document_id, doc_stem, version_no, version_status, langs) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (doc_id, doc_stem, version_no, CURRENT, langs)).fetchone()[0]
    for role in roles:
        conn.execute(
            "INSERT INTO document_roles (document_id, role) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING", (doc_id, role.value))
    conn.commit()
    return RegisterResult(doc_id, ver_id, created=True)


def get_current_version(conn: psycopg.Connection, slug: str) -> VersionRecord | None:
    row = conn.execute(
        f"SELECT {_VERSION_COLS} FROM document_versions v "
        "JOIN documents d ON d.id = v.document_id "
        "WHERE d.slug = %s AND v.version_status = %s",
        (slug, CURRENT)).fetchone()
    return VersionRecord(*row) if row else None


def get_version_by_stem(conn: psycopg.Connection, doc_stem: str) -> VersionRecord | None:
    row = conn.execute(
        f"SELECT {_VERSION_COLS} FROM document_versions v WHERE v.doc_stem = %s",
        (doc_stem,)).fetchone()
    return VersionRecord(*row) if row else None


def allowed_roles(conn: psycopg.Connection, doc_stem: str) -> list[Role]:
    rows = conn.execute(
        "SELECT r.role FROM document_roles r "
        "JOIN document_versions v ON v.document_id = r.document_id "
        "WHERE v.doc_stem = %s ORDER BY r.role", (doc_stem,)).fetchall()
    return [Role(r[0]) for r in rows]


def mark_superseded(conn: psycopg.Connection, *, slug: str, new_doc_stem: str,
                    langs: list[str]) -> RegisterResult:
    """Nuova versione current; la precedente diventa superseded (mai cancellata).

    Ordine obbligato dall'indice one_current_per_document: prima si marca la
    vecchia, poi si inserisce la nuova. Unica transazione.
    """
    old = get_current_version(conn, slug)
    if old is None:
        raise ValueError(f"documento {slug!r} senza versione current da sostituire")
    conn.execute(
        "UPDATE document_versions SET version_status = %s, superseded_at = now() "
        "WHERE id = %s", (SUPERSEDED, old.version_id))
    ver_id = conn.execute(
        "INSERT INTO document_versions "
        "(document_id, doc_stem, version_no, version_status, langs) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (old.document_id, new_doc_stem, old.version_no + 1, CURRENT, langs)).fetchone()[0]
    conn.execute(
        "UPDATE document_versions SET superseded_by_version_id = %s WHERE id = %s",
        (ver_id, old.version_id))
    conn.commit()
    return RegisterResult(old.document_id, ver_id, created=True)


def export_backfill(conn: psycopg.Connection) -> dict[str, BackfillRecord]:
    """Proiezione per il backfill Qdrant (P4.2): doc_stem -> stato/versione/ruoli.

    Copre TUTTE le versioni (current e superseded): i ruoli sono del
    documento logico, quindi identici su ogni versione.
    """
    rows = conn.execute(
        "SELECT v.doc_stem, v.version_status, v.version_no, "
        "       coalesce(array_agg(r.role ORDER BY r.role) "
        "                FILTER (WHERE r.role IS NOT NULL), '{}') "
        "FROM document_versions v "
        "JOIN documents d ON d.id = v.document_id "
        "LEFT JOIN document_roles r ON r.document_id = d.id "
        "GROUP BY v.doc_stem, v.version_status, v.version_no").fetchall()
    return {stem: BackfillRecord(status, ver, [Role(x) for x in roles])
            for stem, status, ver, roles in rows}


def backfill_payload(rec: BackfillRecord) -> dict:
    """Serializza un record SSOT nelle 3 chiavi ADDITIVE del payload Qdrant.

    Fail-closed: ruoli vuoti o stato fuori vocabolario fermano il backfill
    (un doc senza ruoli sarebbe invisibile a tutti col filtro RBAC di P4.5,
    ma un buco qui indica dati SSOT corrotti: meglio fermarsi).
    """
    if rec.version_status not in (CURRENT, SUPERSEDED, DELETED):
        raise ValueError(f"version_status fuori vocabolario: {rec.version_status!r}")
    if not rec.ruoli:
        raise ValueError("record senza ruoli: SSOT incoerente, backfill fermato")
    return {"version_status": rec.version_status,
            "doc_version": rec.doc_version,
            "ruoli_consentiti": [str(r.value) for r in rec.ruoli]}


class SupersedeReport(NamedTuple):
    phase1: str            # "switched" | "already-done"
    sync_reports: list
    warnings: list[str]


def supersede_document(conn: psycopg.Connection, *, slug: str, new_doc_stem: str,
                       langs: list[str], sync) -> SupersedeReport:
    """Flusso "nuova versione", rilanciabile ALLA CIECA dopo qualsiasi crash.

    Fase 1 (verita', atomica): mark_superseded su Postgres — con guardia di
    idempotenza: se new_doc_stem e' gia' la current dello slug, la fase e'
    gia' avvenuta (crash post-commit) e NON si crea una v3.
    Fase 2 (proiezione, convergente): sync(export) riconcilia Qdrant dallo
    stato SSOT; rieseguirla su stato gia' allineato e' un no-op.
    """
    existing = get_version_by_stem(conn, new_doc_stem)
    if existing is None:
        mark_superseded(conn, slug=slug, new_doc_stem=new_doc_stem, langs=langs)
        phase1 = "switched"
    else:
        owner = conn.execute("SELECT slug FROM documents WHERE id = %s",
                             (existing.document_id,)).fetchone()[0]
        if owner != slug or existing.version_status != CURRENT:
            raise ValueError(
                f"stato incoerente: {new_doc_stem!r} e' {existing.version_status} "
                f"di {owner!r}, non current di {slug!r} — switch rifiutato")
        phase1 = "already-done"  # crash post-commit: si riparte dalla sync

    reports = sync(export_backfill(conn))
    warnings = [f"{new_doc_stem}: registrato current ma senza punti in "
                f"{r.collection} — ingerire il documento e rilanciare la sync"
                for r in reports if new_doc_stem in r.missing_stems]
    return SupersedeReport(phase1=phase1, sync_reports=list(reports),
                           warnings=warnings)


def list_documents(conn: psycopg.Connection) -> list[DocumentRecord]:
    rows = conn.execute(
        "SELECT d.id, d.slug, d.category, v.doc_stem FROM documents d "
        "LEFT JOIN document_versions v "
        "ON v.document_id = d.id AND v.version_status = %s "
        "ORDER BY d.slug", (CURRENT,)).fetchall()
    return [DocumentRecord(*r) for r in rows]
