"""Fase 1.3 — Tool SQL sicuro del Cervello Unificatore (Text-to-Parameters).

Il ramo STRUCTURED del grafo interroga il Semantic Layer di 1.2
(`estrazione.righe_listino`) per rispondere a domande su codici/prezzi. Il rischio,
lasciando che un LLM generi SQL, e' doppio: injection (SQL arbitrario) e
allucinazione (una query "plausibile" ma sbagliata). Qui l'LLM **non genera mai
SQL**: si estraggono solo PARAMETRI e si esegue una query template parametrizzata.

Anti-injection, invarianti NON negoziabili:
  - la **colonna** proviene SOLO da `ALLOWED_COLUMNS` (whitelist chiusa) ed e' composta
    con `psycopg.sql.Identifier` — mai dal valore grezzo dell'utente/LLM;
  - il **codice** viaggia SOLO come parametro `%s` (placeholder psycopg), calcolato da
    noi con `codes.canonical` (che riduce l'input a `[0-9A-Z]`: ogni metacarattere SQL
    sparisce prima ancora di diventare parametro);
  - **zero** interpolazione di stringa (niente f-string / `%`-format / `+`) per l'SQL;
  - tabella interrogata: SOLO `righe_listino`. Difesa in profondita' aggiuntiva: al
    server acceso, connessione con ruolo DB **read-only**.

Fail-closed: un codice che si normalizza a vuoto non produce query; un codice assente
in tabella ritorna zero righe -> "non lo so" a valle (mai un prezzo inventato).

Ports & Adapters (come 1.1/1.2): `SqlExecutor` e' il Port; `FakeSqlExecutor` gira in
memoria nei test (DB-free), `PgSqlExecutor` e' l'adapter reale psycopg su
`righe_listino`. Schema `parametrizzabile` per isolare i test (stile `extraction/load`).

Design: docs/superpowers/specs/2026-07-09-fase1.3-cervello-unificatore-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import psycopg
from psycopg import sql

from src.ingestion.codes import canonical

DEFAULT_SCHEMA = "estrazione"

# Whitelist CHIUSA delle colonne interrogabili. Qualsiasi campo fuori da qui e'
# rifiutato prima di toccare l'SQL: e' l'unica sorgente ammessa per un identificatore
# di colonna. Volutamente ristretta ai dati "esponibili" del Semantic Layer (niente
# doc_stem/page/updated_at come *selezione*: quelli sono provenienza, aggiunti da noi).
ALLOWED_COLUMNS: frozenset[str] = frozenset({"prezzo", "descrizione", "codice_originale"})

# Keyword -> campo. Multilingua IT/EN/DE (corpus). Le keyword di PREZZO hanno
# precedenza (una domanda di prezzo e' piu' specifica del generico "what is"); poi le
# keyword di DESCRIZIONE; il default resta `prezzo`, perche' l'intent STRUCTURED
# tipico e' una domanda di prezzo e una query di solo codice cade qui.
_PRICE_KEYWORDS = (
    "prezzo", "costa", "costo", "listino",
    "price", "cost",
    "preis", "kostet", "kosten",
)
_DESC_KEYWORDS = (
    "descrizione", "descrivi", "cos'e", "cos e", "che cosa", "che cos",
    "description", "describe", "what is",
    "beschreibung", "beschreib", "was ist",
)

# Template parametrizzato: `col` e `schema` sono Identifier whitelisted; il codice e'
# l'unico dato utente e passa come `%s`. Mai concatenazione.
# P4.5/A6 — IL FILTRO DI RUOLO E' PARTE DELLA QUERY, non un controllo a valle.
# SPEC §F.3: "un commerciale non recupera i documenti tecnici". Prima del
# 25/08 questo SELECT non toccava `document_roles`, e il ramo strutturato
# scavalcava l'RBAC che Qdrant applica su ogni altro percorso. Exploit
# riprodotto per intero: un `commerciale` che chiedeva `codice 00000001B007`
# riceveva "prezzo 92.00 EUR (Mamoli_Catalogo_Ricambi_2021_IT_EN, pag. 224)"
# a livello **ALTA** — documento tecnico-only, dato piu' sensibile che il
# sistema tratta, massima confidenza. Ampiezza: 168495 righe su 169567 (99.4%)
# appartengono a documenti invisibili al commerciale.
# L'INNER JOIN e' fail-closed per costruzione: un documento senza riga in
# `document_roles` non e' visibile a nessuno, invece di esserlo a tutti.
_SELECT = sql.SQL(
    "SELECT {col}, r.doc_stem, r.page, v.version_status, v.ingested_at "
    "FROM {schema}.righe_listino r "
    "JOIN public.documents d ON d.slug = r.doc_stem "
    "JOIN public.document_roles dr ON dr.document_id = d.id AND dr.role = %s "
    "LEFT JOIN public.document_versions v ON v.doc_stem = r.doc_stem "
    "WHERE r.codice_normalizzato = %s "
    "ORDER BY r.doc_stem, r.page, {col}"
)


@dataclass(frozen=True)
class SqlParams:
    """Parametri estratti dalla query (Text-to-Parameters). Nessun SQL qui dentro."""

    codice_normalizzato: str
    campo: str
    #: Ruolo RBAC della sessione. OBBLIGATORIO e senza default: un default
    #: qui sarebbe una porta aperta silenziosa, ed e' esattamente il modo in
    #: cui questo ramo ha scavalcato l'RBAC fino al 25/08.
    role: str = ""


@dataclass(frozen=True)
class LookupRow:
    """Una riga di risultato: il valore richiesto + la provenienza per la citazione."""

    valore: Any
    doc_stem: str
    page: int
    version_status: str | None = None
    ingested_at: Any | None = None


def extract_params(code_token: str, query: str, role: str = "") -> SqlParams:
    """Deriva i parametri sicuri dalla richiesta. Nessun LLM, nessun SQL.

    Il codice e' normalizzato con `codes.canonical` (riuso, non reinvento): questo
    e' anche la prima barriera anti-injection, perche' riduce l'input a `[0-9A-Z]`.
    Fail-closed: se dopo la normalizzazione non resta nulla, solleva — nessuna query
    su un codice fantasma.
    """
    codice = canonical(code_token)
    if not codice:
        raise ValueError(
            f"code_token {code_token!r} vuoto dopo normalizzazione: fail-closed, nessuna query")
    campo = _detect_campo(query)
    return SqlParams(codice_normalizzato=codice, campo=campo, role=role)


def _detect_campo(query: str) -> str:
    q = (query or "").lower()
    if any(k in q for k in _PRICE_KEYWORDS):
        return "prezzo"
    if any(k in q for k in _DESC_KEYWORDS):
        return "descrizione"
    return "prezzo"


def _build_query(campo: str, schema: str) -> sql.Composed:
    """Compone la query template. La colonna DEVE essere whitelisted (difesa strutturale)."""
    if campo not in ALLOWED_COLUMNS:
        raise ValueError(
            f"campo {campo!r} fuori whitelist {sorted(ALLOWED_COLUMNS)}: query rifiutata")
    return _SELECT.format(col=sql.Identifier(campo), schema=sql.Identifier(schema))


@runtime_checkable
class SqlExecutor(Protocol):
    """Port dell'esecutore: dai parametri sicuri alle righe. Reale vs Fake."""

    def run(self, params: SqlParams) -> list[LookupRow]:
        ...


@dataclass
class FakeSqlExecutor:
    """Esecutore in memoria per i test (DB-free).

    `records`: `codice_normalizzato -> {campo: valore, "doc_stem": ..., "page": ...}`.
    Rispecchia le invarianti del reale: rifiuta un campo fuori whitelist (difesa in
    profondita' anche nel Fake) e ritorna vuoto per un codice assente (fail-closed).
    Registra le chiamate in `calls` per gli assert del grafo.
    """

    records: dict[str, dict] = field(default_factory=dict)
    calls: list[SqlParams] = field(default_factory=list)

    def run(self, params: SqlParams) -> list[LookupRow]:
        self.calls.append(params)
        if params.campo not in ALLOWED_COLUMNS:
            raise ValueError(
                f"campo {params.campo!r} fuori whitelist {sorted(ALLOWED_COLUMNS)}: rifiutato")
        rec = self.records.get(params.codice_normalizzato)
        if rec is None:
            return []
        return [LookupRow(
            valore=rec[params.campo],
            doc_stem=rec["doc_stem"],
            page=rec["page"],
            version_status=rec.get("version_status"),
            ingested_at=rec.get("ingested_at")
        )]


@dataclass
class PgSqlExecutor:
    """Adapter reale: query template parametrizzata su `righe_listino` (psycopg).

    Connessione INIETTATA (convenzione `extraction/load`, `registry`). Schema
    parametrizzabile per i test su schema usa-e-getta. Al server acceso la connessione
    andra' aperta con un ruolo DB read-only (difesa in profondita')."""

    conninfo: str
    schema: str = DEFAULT_SCHEMA

    def run(self, params: SqlParams) -> list[LookupRow]:
        q = _build_query(params.campo, self.schema)
        # Prima di aprire la connessione: senza ruolo non esiste una query
        # legittima da fare, quindi non si tocca nemmeno il DB.
        if not params.role:
            raise ValueError(
                "SqlParams.role vuoto: il ramo strutturato non interroga "
                "il catalogo senza un ruolo (fail-closed, SPEC §F.3)")
        with psycopg.connect(self.conninfo) as conn:
            with conn.cursor() as cur:
                cur.execute(q, (params.role, params.codice_normalizzato))
                return [LookupRow(
                    valore=r[0],
                    doc_stem=r[1],
                    page=r[2],
                    version_status=r[3],
                    ingested_at=r[4]
                ) for r in cur.fetchall()]


def lookup(executor: SqlExecutor, code_token: str, query: str, role: str = "") -> list[LookupRow]:
    """Tie deterministico usato dal `sql_node`: params sicuri -> executor -> righe."""
    return executor.run(extract_params(code_token, query, role))
