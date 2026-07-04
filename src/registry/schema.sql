-- P4.1 — Registro versioni documenti (SSOT). DDL idempotente.
-- Nessun nome di schema esplicito: si applica al search_path corrente
-- (produzione: public; test: schema temporaneo).

-- Identita' LOGICA del documento (il "manuale Mamoli", non il file).
CREATE TABLE IF NOT EXISTS documents (
    id         serial PRIMARY KEY,
    slug       text NOT NULL UNIQUE,
    title      text,
    category   text,
    created_at timestamptz NOT NULL DEFAULT now()
);

-- Versione FISICA: doc_stem = chiave di join verso il payload `doc` di
-- entrambe le collection Qdrant (contratto handoff P3->P4 §2).
CREATE TABLE IF NOT EXISTS document_versions (
    id                       serial PRIMARY KEY,
    document_id              integer NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    doc_stem                 text NOT NULL UNIQUE,
    version_no               integer NOT NULL,
    version_status           text NOT NULL
                             CHECK (version_status IN ('current', 'superseded')),
    langs                    text[] NOT NULL DEFAULT '{}',
    ingested_at              timestamptz NOT NULL DEFAULT now(),
    superseded_at            timestamptz,
    superseded_by_version_id integer REFERENCES document_versions(id),
    UNIQUE (document_id, version_no)
);

-- Regola SPEC §E: al piu' UNA versione current per documento logico.
CREATE UNIQUE INDEX IF NOT EXISTS one_current_per_document
    ON document_versions (document_id)
    WHERE version_status = 'current';

CREATE TABLE IF NOT EXISTS document_roles (
    document_id integer NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    role        text NOT NULL
                CHECK (role IN ('commerciale', 'tecnico', 'amministrazione')),
    PRIMARY KEY (document_id, role)
);
