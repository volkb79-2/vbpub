-- Workflow core tables: PostgreSQL-authoritative durable run/stage/work/
-- attempt lifecycle plus the transactional outbox and result inbox.
--
-- dstdns-P84 (CW1, docs/plan-core-workflow-architecture-2026-08.md §5, §13):
-- PostgreSQL owns lifecycle truth; Redis Streams are at-least-once transport.
-- This is the CW1-scoped foundation only (runs/run_stages/work_units/
-- work_attempts/outbox_events/result_inbox) needed by an ad-hoc echo run.
-- CW2 adds corpus_versions, run_domains, and work_unit_domains once their
-- invariants can be proved by a bounded DNS slice — do not add them here.
--
-- Status vocabulary mirrors common.workflow_registry (the Python source of
-- truth); the CHECK constraints below are the PostgreSQL-side enforcement of
-- the SAME vocabulary so an unknown/free-form status is rejected by the
-- database, not merely by application code (O1).
--
-- Terminal-timestamp convention used throughout this file: `finished_at` is
-- NOT NULL if and only if `status` is one of that table's terminal states.
-- This is enforced with a CHECK constraint on every lifecycle table below.

-- ============================================================================
-- RUNS — one corpus, ad-hoc, or system execution.
-- ============================================================================

CREATE TABLE IF NOT EXISTS runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- CW1 only ever writes 'ad_hoc'; 'corpus' and 'system' are locked into
    -- the target model now (plan §5.1) so later CW packages need no ALTER.
    kind VARCHAR(20) NOT NULL CHECK (kind IN ('ad_hoc', 'corpus', 'system')),
    -- Requested/desired state, distinct from the derived execution state
    -- below (plan §5.1: "stores requested/desired state separately from
    -- derived execution state").
    desired_state VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (desired_state IN ('active', 'paused', 'cancelled')),
    status VARCHAR(20) NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'running', 'succeeded', 'failed', 'cancelled')),
    -- Derived server-side at the authenticated controller boundary (O2); the
    -- public command that creates a run carries none of these fields.
    owner_id VARCHAR(255) NOT NULL,
    owner_type VARCHAR(20) NOT NULL DEFAULT 'user',
    provenance JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    -- CW2a: the immutable corpus version this run executes, and the durable
    -- enumeration state for it. Both live on `runs`, not `run_stages`, because
    -- membership (run_domains) is run-scoped and every stage of a run consumes
    -- the SAME membership (plan §6.3) — a per-stage cursor and a per-stage
    -- ordinal allocator would collide on run_domains' UNIQUE (run_id, ordinal)
    -- the moment a run had two stages. The FK lands in 21 (corpus_versions does
    -- not exist yet at this point in the init order).
    corpus_version_id UUID,
    source_cursor JSONB,
    source_exhausted BOOLEAN NOT NULL DEFAULT FALSE,
    next_domain_ordinal BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT ck_runs_corpus_version_pin CHECK (
        (kind = 'corpus' AND corpus_version_id IS NOT NULL)
        OR (kind <> 'corpus' AND corpus_version_id IS NULL)
    ),
    CONSTRAINT ck_runs_next_domain_ordinal CHECK (next_domain_ordinal >= 0),
    CONSTRAINT ck_runs_cursor_versioned CHECK (
        source_cursor IS NULL
        OR (jsonb_typeof(source_cursor) = 'object' AND source_cursor ? 'cursor_version')
    ),
    -- Only a corpus run enumerates a source. An ad_hoc/system run carrying
    -- cursor state is as wrong as a corpus run without a pinned version.
    CONSTRAINT ck_runs_cursor_only_for_corpus CHECK (
        kind = 'corpus'
        OR (source_cursor IS NULL AND source_exhausted = FALSE AND next_domain_ordinal = 0)
    ),
    CONSTRAINT ck_runs_terminal_timestamp CHECK (
        (status IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NOT NULL)
        OR (status NOT IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_runs_owner ON runs (owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_runs_status ON runs (status);

-- ============================================================================
-- RUN_STAGES — versioned stage instances within a run.
-- ============================================================================

CREATE TABLE IF NOT EXISTS run_stages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    -- CW1's only stage key was 'echo'; CW2a added 'dns'. A2-a (D-056) widens
    -- this to the full §3.4 stage catalog: 'membership' (enumerate),
    -- 'dns' (query_domain_dns), 'web' (scan_web_server/analyze_web_content),
    -- 'classify' (classify_domains), 'report' (aggregate/generate_report).
    -- A3-7 (D-066) adds 'system' — the stage_key of the 6 SYSTEM ops
    -- (discover_dns_servers/test_dns_servers/refresh_dns_server_stats/
    -- refresh_daily_corpus_snapshots/import_ip_ranges/import_threat_feeds).
    -- A2-a widened the operation CHECKs + runs.kind for the system ops but
    -- omitted this stage_key value, so no system op could create a run (its
    -- run_stages INSERT failed the CHECK); D-066 closes that gap.
    -- 'echo' stays as the synthetic test vertical. This CHECK mirrors
    -- common.workflow_registry, which is the Python source of truth (plan
    -- §5.3) — widen both together or neither (A2-b widens the registry).
    stage_key VARCHAR(50) NOT NULL CHECK (
        stage_key IN ('echo', 'membership', 'dns', 'web', 'classify', 'report', 'system')
    ),
    status VARCHAR(20) NOT NULL DEFAULT 'planned'
        CHECK (status IN ('planned', 'running', 'succeeded', 'failed', 'cancelled')),
    -- A3-S2 (D-067): the stage's position in the run's fixed stage plan. The
    -- multi-stage plan is represented as pre-created `planned` run_stages rows
    -- ordered by this column; advancement (advance_ready_stages) promotes the
    -- lowest-ordinal `planned` stage whose predecessor (ordinal-1) has
    -- succeeded. A single-stage run (create_run) uses ordinal 0. SMALLINT is
    -- ample (a workflow plan is a handful of stages). DEFAULT 0 keeps the
    -- single-stage create_run INSERT valid; the CHECK is the one new public
    -- CHECK this package adds (EXPECTED_CHECK_COUNT 40 -> 41).
    stage_ordinal SMALLINT NOT NULL DEFAULT 0 CHECK (stage_ordinal >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    -- Counters are projections, rebuildable from work_units; none are stored
    -- here (plan §5.1: "counters are projections that can be rebuilt").
    UNIQUE (run_id, stage_key),
    -- The stage plan is ordinal-addressed: at most one stage per position, so
    -- advancement's "predecessor = ordinal-1" is unambiguous.
    UNIQUE (run_id, stage_ordinal),
    CONSTRAINT ck_run_stages_terminal_timestamp CHECK (
        (status IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NOT NULL)
        OR (status NOT IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_run_stages_run ON run_stages (run_id);

-- ============================================================================
-- WORK_UNITS — bounded batches of work for one stage.
-- ============================================================================

CREATE TABLE IF NOT EXISTS work_units (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_stage_id UUID NOT NULL REFERENCES run_stages(id) ON DELETE CASCADE,
    -- Immutable operation type (plan §5.1). A2-a (D-056) widens this from
    -- 'echo'-only to the full §3.4 operation catalog: the concrete literals
    -- that exist as operations today, keeping 'echo' as the synthetic test
    -- vertical. This CHECK is a PERMISSIVE SUPERSET of
    -- common.workflow_registry (echo-only until A2-b) — DDL widens ahead of
    -- the registry safely because the DDL only bounds the value set, it does
    -- not dispatch (see escalate_if in the A2-a handoff). Do NOT invent a
    -- 'refresh_*' wildcard — a CHECK IN-list needs concrete literals
    -- (AGENTS.md §4.2a); both refresh ops below are ported worker-db
    -- handlers (corpus_snapshot.py:30, dns_server_stats.py:33).
    operation VARCHAR(50) NOT NULL CHECK (operation IN (
        'echo',
        'enumerate',
        'query_domain_dns',
        'scan_web_server',
        'analyze_web_content',
        'classify_domains',
        'aggregate',
        'generate_report',
        'refresh_daily_corpus_snapshots',
        'refresh_dns_server_stats',
        'discover_dns_servers',
        'test_dns_servers',
        'import_ip_ranges',
        'import_threat_feeds'
    )),
    status VARCHAR(20) NOT NULL DEFAULT 'ready'
        CHECK (status IN ('ready', 'active', 'succeeded', 'failed', 'cancelled')),
    -- Bounded input reference — for echo this IS the small input (the
    -- message); never an embedded domain array (plan §5.1/§7.1).
    input_ref JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CONSTRAINT ck_work_units_terminal_timestamp CHECK (
        (status IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NOT NULL)
        OR (status NOT IN ('succeeded', 'failed', 'cancelled') AND finished_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_work_units_stage ON work_units (run_stage_id);
CREATE INDEX IF NOT EXISTS idx_work_units_status ON work_units (status);

-- ============================================================================
-- WORK_ATTEMPTS — one attempt to execute a work unit.
-- ============================================================================

-- Global monotonic fence sequence. A plain increasing BIGINT (not a UUID)
-- makes "is this fence still current" a simple integer comparison during
-- result-inbox fencing (worker-db) instead of requiring a second lookup.
CREATE SEQUENCE IF NOT EXISTS work_attempt_fence_seq;

CREATE TABLE IF NOT EXISTS work_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    work_unit_id UUID NOT NULL REFERENCES work_units(id) ON DELETE CASCADE,
    attempt_number INT NOT NULL CHECK (attempt_number >= 1),
    fence_token BIGINT NOT NULL DEFAULT nextval('work_attempt_fence_seq'),
    -- Allocated durably in the SAME transaction that creates this attempt
    -- (locked decision #5) and echoed by worker-io in its result envelope;
    -- worker-io never mints one. Doubles as result_inbox's primary key so
    -- redelivery dedup is a plain unique-conflict, not app-level logic.
    result_event_id UUID NOT NULL DEFAULT gen_random_uuid(),
    status VARCHAR(20) NOT NULL DEFAULT 'created'
        CHECK (status IN ('created', 'published', 'running', 'succeeded', 'failed', 'expired', 'cancelled')),
    worker_id VARCHAR(100),
    outcome JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    dispatched_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    -- Absolute deadline for this attempt. Creation writes a provisional
    -- value so the row is always valid; the outbox's FIRST durable claim
    -- atomically resets it to first-dispatch time + configured timeout and
    -- persists the same value into the stable wire envelope before XADD.
    deadline_at TIMESTAMPTZ NOT NULL,
    UNIQUE (work_unit_id, attempt_number),
    UNIQUE (fence_token),
    UNIQUE (result_event_id),
    CONSTRAINT ck_work_attempts_terminal_timestamp CHECK (
        (status IN ('succeeded', 'failed', 'expired', 'cancelled') AND finished_at IS NOT NULL)
        OR (status NOT IN ('succeeded', 'failed', 'expired', 'cancelled') AND finished_at IS NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_work_attempts_work_unit ON work_attempts (work_unit_id);
-- Reconciler scan target: only non-terminal attempts past their deadline.
CREATE INDEX IF NOT EXISTS idx_work_attempts_deadline_scan
    ON work_attempts (deadline_at)
    WHERE status IN ('published', 'running');

-- ============================================================================
-- OUTBOX_EVENTS — immutable dispatch payload + publish state, created in the
-- same transaction as its work_attempt.
-- ============================================================================

CREATE TABLE IF NOT EXISTS outbox_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- One outbox row per attempt: a retry creates a NEW attempt and a NEW
    -- outbox row, never a second row for the same attempt.
    work_attempt_id UUID NOT NULL REFERENCES work_attempts(id) ON DELETE CASCADE,
    -- Deterministic identity independent of `id`, so an accidental duplicate
    -- creation attempt for the same attempt is rejected by PostgreSQL even
    -- if it would have generated a different `id` (O1's "semantic-outbox-
    -- key" requirement). Computed by the application as
    -- f"{operation}:{work_unit_id}:{attempt_number}".
    semantic_key VARCHAR(255) NOT NULL,
    -- Mirrors work_units.operation's catalog widening (D-056) — see that
    -- CHECK's comment for the full rationale.
    operation VARCHAR(50) NOT NULL CHECK (operation IN (
        'echo',
        'enumerate',
        'query_domain_dns',
        'scan_web_server',
        'analyze_web_content',
        'classify_domains',
        'aggregate',
        'generate_report',
        'refresh_daily_corpus_snapshots',
        'refresh_dns_server_stats',
        'discover_dns_servers',
        'test_dns_servers',
        'import_ip_ranges',
        'import_threat_feeds'
    )),
    envelope_version INT NOT NULL DEFAULT 1 CHECK (envelope_version IN (1)),
    -- D-058: the result-payload contract version (independent of
    -- envelope_version) expected at dispatch. DEFAULT 1 is LOAD-BEARING: the
    -- INSERT sites (workflow_outbox.py, scope.forbid to A2-a) use named-column
    -- inserts that omit this column today, and are exercised only by the
    -- integration lane, which is NOT in A2-a's gate — a NOT NULL column
    -- without a default would go green here and break downstream at
    -- integration time. A2-b sets it explicitly once contracts exist.
    result_payload_version SMALLINT NOT NULL DEFAULT 1
        CHECK (result_payload_version >= 1),
    payload JSONB NOT NULL,
    published BOOLEAN NOT NULL DEFAULT FALSE,
    published_at TIMESTAMPTZ,
    -- Durable claim/lease for the horizontally-safe dispatcher (O3): a
    -- dispatcher claims unpublished/lease-expired rows with
    -- `FOR UPDATE SKIP LOCKED` plus this lease, so two concurrent
    -- dispatchers never publish the same row twice under normal operation,
    -- and a crashed dispatcher's claim is recovered once the lease expires.
    claimed_by VARCHAR(100),
    claimed_at TIMESTAMPTZ,
    lease_expires_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (semantic_key),
    UNIQUE (work_attempt_id),
    CONSTRAINT ck_outbox_events_published_timestamp CHECK (
        (published = TRUE AND published_at IS NOT NULL)
        OR (published = FALSE AND published_at IS NULL)
    )
);

-- Dispatcher claim scan target: only unpublished rows.
CREATE INDEX IF NOT EXISTS idx_outbox_events_unpublished
    ON outbox_events (created_at)
    WHERE published = FALSE;

-- ============================================================================
-- RESULT_INBOX — unique result-event ID, attempt/fence, acceptance outcome.
-- ============================================================================

CREATE TABLE IF NOT EXISTS result_inbox (
    -- The result-event ID minted on work_attempts.result_event_id at
    -- create-run time. Using it as the primary key makes redelivery
    -- dedup an atomic `INSERT ... ON CONFLICT (id) DO NOTHING` — no
    -- separate existence check is needed or possible to race.
    id UUID PRIMARY KEY,
    work_attempt_id UUID NOT NULL REFERENCES work_attempts(id) ON DELETE CASCADE,
    work_unit_id UUID NOT NULL REFERENCES work_units(id) ON DELETE CASCADE,
    -- Mirrors work_units.operation's catalog widening (D-056) — see that
    -- CHECK's comment for the full rationale.
    operation VARCHAR(50) NOT NULL CHECK (operation IN (
        'echo',
        'enumerate',
        'query_domain_dns',
        'scan_web_server',
        'analyze_web_content',
        'classify_domains',
        'aggregate',
        'generate_report',
        'refresh_daily_corpus_snapshots',
        'refresh_dns_server_stats',
        'discover_dns_servers',
        'test_dns_servers',
        'import_ip_ranges',
        'import_threat_feeds'
    )),
    envelope_version INT NOT NULL DEFAULT 1 CHECK (envelope_version IN (1)),
    -- D-058: the result-payload contract version the worker DECLARED.
    -- worker-db validates this equals outbox_events.result_payload_version
    -- at the inbox boundary (A2-b). DEFAULT 1 is LOAD-BEARING for the same
    -- reason as outbox_events.result_payload_version above — see that
    -- column's comment.
    result_payload_version SMALLINT NOT NULL DEFAULT 1
        CHECK (result_payload_version >= 1),
    attempt_number INT NOT NULL CHECK (attempt_number >= 1),
    -- Attempt number and fence echoed back by the worker; compared against
    -- the attempt's
    -- CURRENT fence at ingestion time. A result whose fence no longer
    -- matches (the attempt was superseded by a retry) is retained here as
    -- a non-canonical audit row (`accepted = false`) and cannot advance
    -- lifecycle state (plan §5.2, §9.1).
    fence_token BIGINT NOT NULL,
    accepted BOOLEAN NOT NULL,
    reject_reason TEXT,
    outcome JSONB,
    error TEXT,
    -- Preserve the parsed wire envelope that drove the decision.  A
    -- malformed lineage delivery can legitimately arrive first under the
    -- preallocated result_event_id; a later correct redelivery upgrades the
    -- same inbox row to canonical without erasing the bad delivery from this
    -- append-only JSON audit array.
    payload JSONB NOT NULL,
    rejected_deliveries JSONB NOT NULL DEFAULT '[]'::jsonb
        CHECK (jsonb_typeof(rejected_deliveries) = 'array'
               AND jsonb_array_length(rejected_deliveries) <= 32),
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT ck_result_inbox_reject_reason CHECK (
        (accepted = TRUE AND reject_reason IS NULL)
        OR (accepted = FALSE AND reject_reason IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_result_inbox_work_attempt ON result_inbox (work_attempt_id);

-- At most one canonical accepted result per work unit (O1's "accepted-result
-- constraint" — two canonical accepted outcomes for one work unit must be
-- rejected by PostgreSQL, not merely by Python).
CREATE UNIQUE INDEX IF NOT EXISTS uq_result_inbox_accepted_per_work_unit
    ON result_inbox (work_unit_id)
    WHERE accepted;
