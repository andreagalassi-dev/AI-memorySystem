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

-- P7 — Watchdog / Auto-Ingestion
-- Migrazione idempotente: aggiorna il constraint di version_status per accettare 'deleted'
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_name = 'document_versions'
          AND constraint_name = 'document_versions_version_status_check'
    ) THEN
        ALTER TABLE document_versions
            DROP CONSTRAINT document_versions_version_status_check;
    END IF;
    ALTER TABLE document_versions
        ADD CONSTRAINT document_versions_version_status_check
        CHECK (version_status IN ('current', 'superseded', 'deleted'));
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- Traccia lo stato fisico dei file nel corpus (P7).
-- FK su documents.id (logico), NON su document_versions.id (fisico).
CREATE TABLE IF NOT EXISTS tracked_files (
    id                  serial PRIMARY KEY,
    filename            text NOT NULL UNIQUE,
    file_hash           text NOT NULL,           -- SHA-256 del contenuto
    file_size           bigint,                  -- pre-filtro Fase 1
    file_mtime          timestamptz,             -- pre-filtro Fase 1
    status              text NOT NULL
                        CHECK (status IN ('queued', 'processing', 'active', 'deleted', 'failed')),
    document_id         integer REFERENCES documents(id) ON DELETE SET NULL,
    last_processed_at   timestamptz NOT NULL DEFAULT now(),
    queued_at           timestamptz,
    deleted_at          timestamptz,
    error_log           text,
    retry_count         integer NOT NULL DEFAULT 0,
    profile_warning     boolean NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS tracked_files_status_idx ON tracked_files(status);
CREATE INDEX IF NOT EXISTS tracked_files_document_id_idx ON tracked_files(document_id);
