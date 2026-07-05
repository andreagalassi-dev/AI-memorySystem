-- Debito tecnico §1 — Profili di Ingestion per-brand su PostgreSQL (SSOT).
-- DDL idempotente. Nessun nome di schema esplicito: si applica al search_path
-- corrente (produzione: public; test: schema temporaneo), come schema.sql P4.1.
-- config/brands.json resta nel repo SOLO come seed (scripts/seed_ingestion_profiles.py).
-- Fase 0 single-tenant: niente tenant_id; a P7 basta aggiungere la colonna e
-- passare UNIQUE(name) -> UNIQUE(tenant_id, name).

CREATE TABLE IF NOT EXISTS ingestion_profiles (
    id             serial PRIMARY KEY,
    name           text NOT NULL UNIQUE,       -- 'mamoli', 'electrolux', 'default'
    short_pattern  text,                       -- regex famiglia corta (ancorata), NULL se assente
    reason_short   text,
    unsafe_numeric text,                       -- regex candidati unsafe: SOLO log, mai applicati
    reason_unsafe  text,
    passthrough    boolean NOT NULL DEFAULT false,  -- true solo per 'default'
    is_active      boolean NOT NULL DEFAULT true,
    created_at     timestamptz NOT NULL DEFAULT now(),
    updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Famiglie-codice lunghe del profilo (0..N, es. 'V0-lunga').
-- position: l'ordine conta — il motore si ferma alla PRIMA famiglia che matcha.
CREATE TABLE IF NOT EXISTS ingestion_code_families (
    id              serial PRIMARY KEY,
    profile_id      integer NOT NULL REFERENCES ingestion_profiles(id) ON DELETE CASCADE,
    name            text NOT NULL,             -- 'V0-lunga', '0US-pnc'
    extract_pattern text NOT NULL,             -- tollerante a spazi/markup, NON ancorata
    token_pattern   text NOT NULL,             -- forma canonica ancorata (router/chunker)
    rejoin          boolean NOT NULL DEFAULT false,
    reason_rejoin   text,
    reason_double   text,
    position        integer NOT NULL DEFAULT 0,
    UNIQUE (profile_id, name)
);

-- Mappa documento -> profilo (glob fnmatch sul nome documento).
-- priority: risoluzione deterministica quando piu' glob matchano.
CREATE TABLE IF NOT EXISTS ingestion_doc_map (
    id           serial PRIMARY KEY,
    glob_pattern text NOT NULL UNIQUE,         -- 'Mamoli_*', 'MAMOLI_*'
    profile_id   integer NOT NULL REFERENCES ingestion_profiles(id) ON DELETE CASCADE,
    priority     integer NOT NULL DEFAULT 0
);

-- Debito §4 — riparazione "ultima ratio" (OFF di default, per-profilo).
-- fuzzy_repair: abilita il pass di riparazione (confusable 1-char, row-rejoin
-- su riga con prezzi mascherati, prefix-restore). I codici riparati sono
-- SEMPRE classificati fuzzy_repaired e a valle cappati a MEDIA, mai ALTA.
-- ALTER idempotenti: le tabelle possono preesistere alla colonna.
ALTER TABLE ingestion_profiles
    ADD COLUMN IF NOT EXISTS fuzzy_repair boolean NOT NULL DEFAULT false;
-- restore_prefix: prefisso di famiglia restaurabile su run numerici orfani
-- (es. 'V0' Mamoli: '94094 00000' -> V09409400000). NULL = mai.
ALTER TABLE ingestion_code_families
    ADD COLUMN IF NOT EXISTS restore_prefix text;
