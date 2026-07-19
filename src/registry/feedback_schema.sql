CREATE TABLE IF NOT EXISTS feedback (
    id serial PRIMARY KEY,
    created_at timestamptz NOT NULL DEFAULT now(),
    utente text NOT NULL,
    ruolo text NOT NULL,
    query text NOT NULL,
    risposta text NOT NULL,
    final_level text,
    citazioni jsonb,
    contesto jsonb,
    verdetto text NOT NULL CHECK (verdetto IN ('conferma', 'correggi', 'segnala')),
    correzione text,
    livello_memoria text CHECK (livello_memoria IN ('personale', 'categoria', 'aziendale')),
    conferme_count int NOT NULL DEFAULT 0,
    stato_moderazione text NOT NULL DEFAULT 'pending' CHECK (stato_moderazione IN ('pending', 'approved', 'rejected'))
);

CREATE INDEX IF NOT EXISTS feedback_utente_created_at_idx ON feedback (utente, created_at);
