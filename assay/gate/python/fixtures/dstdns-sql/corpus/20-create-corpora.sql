-- Migration 20: Create the corpora table (CW2a survivor).
-- The Gen-1 `corpus_runs` and `job_domain_scope` tables were removed in the A1b
-- demolition (D-030) — they linked corpora to the deleted tasks/jobs model.
-- A3 rebuilds "launch a corpus run" onto the Gen-2 runs model.
-- All pure additive DDL (no modifications to existing tables)

CREATE TABLE IF NOT EXISTS corpora (
    id            UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
    name          VARCHAR(200)  NOT NULL,
    description   TEXT,
    version       INTEGER       NOT NULL DEFAULT 1,
    owner_id      VARCHAR(255),
    owner_type    VARCHAR(20),
    rule_sets     JSONB         NOT NULL DEFAULT '[]',
    estimated_domain_count  BIGINT,
    scan_preset   VARCHAR(100),
    scan_config   JSONB,
    lifecycle     VARCHAR(20)   NOT NULL DEFAULT 'draft',
    -- CW2a: pointer to the corpus's current immutable version. Declared here
    -- rather than ALTERed in from 21, because this is the table's own column
    -- and fresh-init DDL should read as one definition; only its FK is deferred
    -- (corpus_versions is created later in the init order).
    current_version_id UUID,
    archived_at   TIMESTAMPTZ,
    created_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    UNIQUE(owner_id, name)
);
CREATE INDEX IF NOT EXISTS idx_corpora_owner ON corpora (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_corpora_lifecycle ON corpora (lifecycle);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'classification_tasks_corpus_id_fkey'
    ) THEN
        ALTER TABLE classification_tasks
            ADD CONSTRAINT classification_tasks_corpus_id_fkey
            FOREIGN KEY (corpus_id) REFERENCES corpora(id) ON DELETE SET NULL;
    END IF;
END
$$;

-- corpus_runs and job_domain_scope removed (A1b, D-030): both keyed on the
-- deleted Gen-1 tasks/jobs tables. A3 re-introduces corpus-run tracking on the
-- Gen-2 runs model.
