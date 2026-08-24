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

-- provenienza (Epica H, cascata web): da dove viene la risposta valutata --
-- 'corpus' | 'web' | 'misto' | 'nessuna'. NIENTE CHECK, come final_level:
-- testo libero, tollerante a un valore nuovo che la cascata web puo'
-- introdurre (es. il terzo livello "conoscenza LLM verificata online" del
-- piano). ALTER idempotente: la tabella puo' preesistere alla colonna.
-- Il motivo di questa colonna e' che la promozione feedback->memoria (F.2b,
-- futura) legge dal record: se la provenienza vivesse solo nella grafica
-- dell'interfaccia, si perderebbe li' e una risposta presa da internet
-- potrebbe salire in memoria aziendale senza che nessuno sappia piu' da
-- dove viene.
ALTER TABLE feedback
    ADD COLUMN IF NOT EXISTS provenienza text NOT NULL DEFAULT 'corpus';
