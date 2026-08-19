-- Corpus versions, normalized membership, and durable run membership (CW2a).
--
-- dstdns CW2a (docs/proposals/cw2-p85-wave/CONTRACT-CW2A.md §1.3). Runs after
-- 20-create-corpora.sql (corpus_versions -> corpora) and after
-- 04-create-tables-dns.sql (run_domains -> domains). 03c declares runs'
-- corpus/cursor columns; the FK lands here because corpus_versions does not
-- exist at 03c time.
--
-- NOT here: work_unit_domains and any admission/fairness state. Work scope is
-- created by the scheduler, which is CW2b. Keeping it out is what keeps a
-- run-global scope constraint from making multi-stage workflows unreachable.
--
-- Idempotent, like every script in this directory (run-schema.sh:22-23 promises
-- it and runs under ON_ERROR_STOP=1). A bare ALTER TABLE ... ADD CONSTRAINT is
-- NOT idempotent, so the two FKs below use the same pg_constraint guard
-- 20-create-corpora.sql already uses for its own deferred constraint.

-- ============================================================================
-- CORPUS_VERSIONS — immutable source/workflow configuration for one corpus.
-- ============================================================================

CREATE TABLE IF NOT EXISTS corpus_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    corpus_id UUID NOT NULL REFERENCES corpora(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version >= 1),
    -- A version's SOURCE is an ordered list of rules, in corpus_version_rules.
    -- Not a single (kind, spec) pair: the product's own model is an ORDERED
    -- ARRAY of rules (corpora.rule_sets, and the UI's four forms), and a
    -- single-source column cannot represent a composite corpus at all. The
    -- flattened ordinal space is the concatenation of the rules' blocks in
    -- rule_index order — the same odometer as charset_length, one level up.
    -- Versioned workflow template. A vocabulary, not a free-form label: an
    -- unbounded VARCHAR here is a value an implementer invents.
    workflow_key VARCHAR(50) NOT NULL
        CHECK (workflow_key IN ('corpus-dns-v1')),
    -- An ESTIMATE, shown as one. Completion never reads it (plan §6.2, §13.9).
    estimated_domain_count BIGINT
        CHECK (estimated_domain_count IS NULL OR estimated_domain_count >= 0),
    complete BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    UNIQUE (corpus_id, version),
    CONSTRAINT ck_corpus_versions_complete_timestamp CHECK (
        (complete = TRUE AND completed_at IS NOT NULL)
        OR (complete = FALSE AND completed_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_corpus_versions_corpus
    ON corpus_versions (corpus_id, version DESC);

-- Immutability is enforced by the DATABASE, not by application discipline.
-- Column grants cannot do it: controller holds ALL PRIVILEGES
-- (90-grant-permissions.sh), so only a trigger binds every writer.
CREATE OR REPLACE FUNCTION corpus_versions_reject_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.corpus_id IS DISTINCT FROM OLD.corpus_id
       OR NEW.version IS DISTINCT FROM OLD.version
       OR NEW.workflow_key IS DISTINCT FROM OLD.workflow_key
       OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'corpus_versions is immutable: only complete/completed_at/estimated_domain_count may change (attempted on id=%)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.complete = TRUE THEN
        -- Completion is settled, not merely monotonic: guarding `complete`
        -- alone still let completed_at be rewritten backwards indefinitely on
        -- a row calling itself immutable.
        IF NEW.complete = FALSE THEN
            RAISE EXCEPTION 'corpus_versions.complete is monotonic (attempted un-complete on id=%)', OLD.id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
            RAISE EXCEPTION 'corpus_versions.completed_at is settled once complete (attempted rewrite on id=%)', OLD.id
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_corpus_versions_immutable ON corpus_versions;
CREATE TRIGGER trg_corpus_versions_immutable
    BEFORE UPDATE ON corpus_versions
    FOR EACH ROW EXECUTE FUNCTION corpus_versions_reject_mutation();

-- A frozen version must not be deleted out from under the runs and evidence
-- that reference it.
--
-- `WHEN (pg_trigger_depth() = 0)` is load-bearing and was NOT in the first
-- draft of this file. Row-level BEFORE DELETE triggers fire on FK cascade, so
-- an unguarded version of this made `DELETE FROM corpora` fail forever once any
-- of its versions completed — permanently undeletable by every application
-- route, escapable only by DISABLE TRIGGER — and it silently turned the
-- ON DELETE CASCADE above into dead DDL. The guard restricts this to DIRECT
-- deletes, which is the case it is actually about. Deleting a corpus still
-- cascades; a run still pins its version via fk_runs_corpus_version's
-- ON DELETE RESTRICT, which is the invariant that protects history.
CREATE OR REPLACE FUNCTION corpus_versions_reject_delete()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'corpus_versions is append-only: a complete version cannot be deleted directly (id=%)', OLD.id
        USING ERRCODE = 'restrict_violation';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_corpus_versions_no_delete ON corpus_versions;
CREATE TRIGGER trg_corpus_versions_no_delete
    BEFORE DELETE ON corpus_versions
    FOR EACH ROW
    WHEN (OLD.complete = TRUE AND pg_trigger_depth() = 0)
    EXECUTE FUNCTION corpus_versions_reject_delete();


-- ============================================================================
-- CORPUS_VERSION_RULES — the ordered source rules of one immutable version.
-- ============================================================================
--
-- A corpus is a SUM of rules, not one rule. Several charset/tld generators and
-- several lists compose; the version's ordinal space is their concatenation in
-- rule_index order, so ordinal N is resolved by finding the rule whose block
-- contains N and delegating with a local ordinal. Direct addressing survives
-- composition exactly as it survives length-blocks inside one charset rule.
--
-- `cardinality` is NOT NULL because a rule whose size is unknown cannot
-- participate in ordinal addressing at all. That is what forces regex_pattern
-- to be materialized into corpus_version_domains at version-creation time
-- (bounded by its own required max_generate) rather than enumerated lazily —
-- once materialized it has a count like any list.
--
-- Block offsets are DERIVED, never stored:
--   SELECT rule_index, cardinality,
--          SUM(cardinality) OVER (ORDER BY rule_index) - cardinality AS ordinal_offset
--     FROM corpus_version_rules WHERE corpus_version_id = $1 ORDER BY rule_index;
-- A stored offset would be a second copy of a fact the cardinalities already
-- determine, and the only way it can be wrong is silently (AGENTS.md §4.2a).

CREATE TABLE IF NOT EXISTS corpus_version_rules (
    corpus_version_id UUID NOT NULL REFERENCES corpus_versions(id) ON DELETE CASCADE,
    rule_index INTEGER NOT NULL CHECK (rule_index >= 0),
    -- The product's four forms, named as the UI names them. `tld_charset` is
    -- the same generator the legacy code calls `charset_length`; the UI's
    -- spelling wins because it is the one users see.
    rule_kind VARCHAR(30) NOT NULL CHECK (
        rule_kind IN ('explicit_list', 'tld_charset', 'wordlist_tld', 'regex_pattern')
    ),
    rule_spec JSONB NOT NULL,
    cardinality BIGINT NOT NULL CHECK (cardinality >= 0),
    PRIMARY KEY (corpus_version_id, rule_index)
);

-- Rules are as immutable as the version that owns them; a version whose rule
-- list can change is not a version. Cascade-exempt for the same reason as the
-- membership guard below.
CREATE OR REPLACE FUNCTION corpus_version_rules_reject_mutation()
RETURNS TRIGGER AS $$
DECLARE
    is_complete BOOLEAN;
    target UUID;
BEGIN
    target := COALESCE(OLD.corpus_version_id, NEW.corpus_version_id);
    SELECT complete INTO is_complete FROM corpus_versions WHERE id = target;
    IF is_complete THEN
        RAISE EXCEPTION 'corpus_version_rules is append-only: the rule list of a complete version cannot be changed (corpus_version_id=%)', target
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_corpus_version_rules_frozen ON corpus_version_rules;
CREATE TRIGGER trg_corpus_version_rules_frozen
    BEFORE INSERT OR UPDATE OR DELETE ON corpus_version_rules
    FOR EACH ROW
    WHEN (pg_trigger_depth() = 0)
    EXECUTE FUNCTION corpus_version_rules_reject_mutation();

-- ============================================================================
-- CORPUS_VERSION_DOMAINS — normalized explicit membership, ordinal-addressed.
-- ============================================================================

CREATE TABLE IF NOT EXISTS corpus_version_domains (
    corpus_version_id UUID NOT NULL REFERENCES corpus_versions(id) ON DELETE CASCADE,
    -- The FLATTENED version-level ordinal, not a per-rule one: materialized
    -- rules occupy their own block of the version's single ordinal space, so
    -- one lookup resolves any ordinal regardless of which rule produced it.
    -- The PRIMARY KEY IS the keyset index (index-only range scan).
    ordinal BIGINT NOT NULL CHECK (ordinal >= 0),
    domain_name VARCHAR(255) NOT NULL,
    -- Provenance: which rule contributed this domain. Composition is exactly
    -- why this is worth keeping — with several lists in one version, "where did
    -- this domain come from" is otherwise unanswerable.
    rule_index INTEGER NOT NULL CHECK (rule_index >= 0),
    PRIMARY KEY (corpus_version_id, ordinal),
    -- Deduplication is WITHIN a rule, not across the version: two rules may
    -- legitimately generate the same domain, and collapsing that at version
    -- level would corrupt the block cardinalities the odometer depends on.
    -- Cross-rule dedup happens once, durably, at run_domains.
    UNIQUE (corpus_version_id, rule_index, domain_name),
    FOREIGN KEY (corpus_version_id, rule_index)
        REFERENCES corpus_version_rules (corpus_version_id, rule_index) ON DELETE CASCADE
);

-- Membership of a COMPLETE version is append-only too. Without this the
-- immutability guarantee is one table short: a complete version's rows could be
-- deleted, leaving a version that claims a source and enumerates nothing.
-- Cascade from corpus_versions is exempt for the same reason as above.
CREATE OR REPLACE FUNCTION corpus_version_domains_reject_mutation()
RETURNS TRIGGER AS $$
DECLARE
    is_complete BOOLEAN;
    target UUID;
BEGIN
    target := COALESCE(OLD.corpus_version_id, NEW.corpus_version_id);
    SELECT complete INTO is_complete FROM corpus_versions WHERE id = target;
    IF is_complete THEN
        RAISE EXCEPTION 'corpus_version_domains is append-only: membership of a complete version cannot be changed (corpus_version_id=%)', target
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_corpus_version_domains_frozen ON corpus_version_domains;
CREATE TRIGGER trg_corpus_version_domains_frozen
    BEFORE INSERT OR UPDATE OR DELETE ON corpus_version_domains
    FOR EACH ROW
    WHEN (pg_trigger_depth() = 0)
    EXECUTE FUNCTION corpus_version_domains_reject_mutation();

-- A complete version with no rules would be a source that enumerates nothing
-- while claiming to be ready — the zero-item shape this project has shipped
-- before. Checked on the completion flip, the one moment it becomes meaningful.
CREATE OR REPLACE FUNCTION corpus_versions_require_rules()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.complete = TRUE AND OLD.complete = FALSE THEN
        IF NOT EXISTS (SELECT 1 FROM corpus_version_rules WHERE corpus_version_id = NEW.id) THEN
            RAISE EXCEPTION 'corpus_versions: a complete version must have at least one source rule (id=%)', NEW.id
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_corpus_versions_require_rules ON corpus_versions;
CREATE TRIGGER trg_corpus_versions_require_rules
    BEFORE UPDATE ON corpus_versions
    FOR EACH ROW EXECUTE FUNCTION corpus_versions_require_rules();

-- ============================================================================
-- RUN_DOMAINS — durable, deduplicated run membership with source provenance.
-- ============================================================================

CREATE TABLE IF NOT EXISTS run_domains (
    id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    -- Dense, 0-based, monotonic within a RUN (not a stage): every stage of a run
    -- consumes the same membership, so one enumeration and one allocator.
    ordinal BIGINT NOT NULL CHECK (ordinal >= 0),
    domain_name VARCHAR(255) NOT NULL,
    -- Resolved lazily: the domains row may not exist until DNS work observes
    -- it. NULL is a legitimate state, not a missing fact.
    domain_id INTEGER REFERENCES domains(id) ON DELETE SET NULL,
    source_ordinal BIGINT NOT NULL CHECK (source_ordinal >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (run_id, ordinal),
    -- Deduplication is a DATABASE fact, which is what makes enumeration replay
    -- safe by construction rather than by application care.
    UNIQUE (run_id, domain_name)
);

CREATE INDEX IF NOT EXISTS idx_run_domains_domain ON run_domains (domain_id)
    WHERE domain_id IS NOT NULL;

-- ============================================================================
-- The corpus-version PIN, and the enumeration cursor, are actually pinned.
-- ============================================================================
--
-- `runs.corpus_version_id` being a FK to an immutable row does not make the
-- REFERENCE immutable: without this, a run could be re-pointed to a different
-- version mid-flight, and a cursor addressing version A's ordinal space would
-- silently continue against version B's. `source_exhausted` was likewise
-- rewindable TRUE->FALSE and `next_domain_ordinal` resettable — so "resume
-- continues from a durable cursor" (plan §13.9) rested on application care
-- rather than on the database.
--
-- Monotonic, not merely non-null: enumeration only ever moves forward, and a
-- backwards move is the shape a replay bug takes.
CREATE OR REPLACE FUNCTION runs_reject_lineage_mutation()
RETURNS TRIGGER AS $$
BEGIN
    IF OLD.corpus_version_id IS NOT NULL
       AND NEW.corpus_version_id IS DISTINCT FROM OLD.corpus_version_id THEN
        RAISE EXCEPTION 'runs.corpus_version_id is pinned once set (run id=%)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.source_exhausted = TRUE AND NEW.source_exhausted = FALSE THEN
        RAISE EXCEPTION 'runs.source_exhausted is monotonic (run id=%)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.next_domain_ordinal < OLD.next_domain_ordinal THEN
        RAISE EXCEPTION 'runs.next_domain_ordinal is monotonic: % -> % (run id=%)',
            OLD.next_domain_ordinal, NEW.next_domain_ordinal, OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_runs_lineage_pinned ON runs;
CREATE TRIGGER trg_runs_lineage_pinned
    BEFORE UPDATE ON runs
    FOR EACH ROW EXECUTE FUNCTION runs_reject_lineage_mutation();

-- ============================================================================
-- Deferred foreign keys (guarded — see the idempotency note at the top).
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_runs_corpus_version') THEN
        ALTER TABLE runs
            ADD CONSTRAINT fk_runs_corpus_version
            FOREIGN KEY (corpus_version_id) REFERENCES corpus_versions(id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_corpora_current_version') THEN
        ALTER TABLE corpora
            ADD CONSTRAINT fk_corpora_current_version
            FOREIGN KEY (current_version_id) REFERENCES corpus_versions(id) ON DELETE RESTRICT;
    END IF;
END
$$;
