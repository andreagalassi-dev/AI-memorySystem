-- Debito tecnico §2 — Dizionario sinonimi tecnico per la Query Expansion.
-- DDL idempotente, si applica al search_path corrente (pattern debito §1).
-- config/synonyms.json e' il SEED (scripts/seed_synonyms.py); a runtime la
-- fonte e' il DB. Lingue: quelle del corpus REALE indicizzato (IT/EN/DE piu'
-- ES/FR presenti in Electrolux ES e Lusben FR); '*' = termine cross-lingua.

CREATE TABLE IF NOT EXISTS synonym_concepts (
    id         serial PRIMARY KEY,
    slug       text NOT NULL UNIQUE,      -- 'boccola', 'spia-livello'
    note       text,
    is_active  boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Termini del concetto (anche multi-parola, SEMPRE lowercase).
-- source/approved: le proposte del futuro estrattore LLM offline entrano come
-- ('llm_proposed', false) e NON vengono usate finche' un umano non le approva.
CREATE TABLE IF NOT EXISTS synonym_terms (
    id         serial PRIMARY KEY,
    concept_id integer NOT NULL REFERENCES synonym_concepts(id) ON DELETE CASCADE,
    term       text NOT NULL CHECK (term = lower(term) AND term != ''),
    lang       text NOT NULL CHECK (lang IN ('IT', 'EN', 'DE', 'ES', 'FR', '*')),
    source     text NOT NULL DEFAULT 'manual'
               CHECK (source IN ('manual', 'llm_proposed')),
    approved   boolean NOT NULL DEFAULT true,
    position   integer NOT NULL DEFAULT 0,   -- ordine deterministico d'espansione
    UNIQUE (concept_id, term)
);

CREATE INDEX IF NOT EXISTS synonym_terms_term_idx ON synonym_terms (term);
