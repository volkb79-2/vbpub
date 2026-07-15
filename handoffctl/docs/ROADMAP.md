# handoffctl roadmap

Status: **design / pilot**. No phase is complete merely because it appears
below. Evidence links and dates must be added when implementation begins.

## Principles

- Read and audit before automating.
- Shadow the existing workflow before taking authority.
- Automate deterministic plumbing first; keep review and merge conservative.
- Advance one risk boundary at a time.
- Stop when evidence disagrees instead of teaching the scheduler to guess.

## Phase 0 — design freeze and naming

Deliverables:

- Ratify project name, process/AI boundary, state model, and source hierarchy.
- Record an ADR for SQLite/event sourcing and the single-host pilot boundary.
- Decide the first pilot project and active milestone.
- Define what remains human-authorized during the pilot.

Exit: architecture and spec reviewed; unresolved product choices are explicit.

## Phase 1 — schemas and read-only drift audit

Deliverables:

- Versioned project, handoff, and event schemas.
- Transition and identifier validation library.
- Read-only importers for current groop and dstdns handoff headers, reports,
  decisions, dispatch snapshots, branches, and worktrees.
- Drift report for stale references, conflicting policy, missing artifacts,
  unknown states, duplicate IDs, stale worktrees, and unbound evidence.

The importer must never edit a consumer repository.

Exit: deterministic import report with no silent coercion.

## Phase 2 — event store and read-only dashboard

Deliverables:

- SQLite WAL event store and rebuildable projections.
- Artifact registry and redaction pipeline.
- Read-only JSON API and SSE stream.
- Active/completed tables, task drill-down, dependency DAG, timeline, leases,
  gates, costs, decisions, provider status, and worktree inventory.
- Loopback-only deployment and security tests.

Exit: dashboard represents imported/manual activity without any AI call.

## Phase 3 — observer wrappers and shadow mode

Deliverables:

- Fixture-backed adapters for Claude, Codex, OpenCode, and Reasonix.
- Normalize process/session, usage, actual/estimated cost, completion, limits,
  and errors.
- Observe manually launched attempts and compare expected transitions.
- Deterministic stall detection without automatic interruption.
- Notification adapter interface and local test notifier.

Exit: shadow state agrees with operator truth across representative runs.

## Phase 4 — deterministic dispatch pilot

Deliverables:

- Worktree lifecycle and exact base-commit binding.
- Capability/provider preflight.
- DAG scheduling, project fairness, WIP caps, retry budgets, and leases.
- Launch/resume/interrupt through adapters.
- Project gate adapter and evidence receipts.
- Cleanup eligibility, but no automatic deletion without retention approval.

Pilot scope: one low-risk, non-live-stack project wave. Frontier review and
merge remain explicitly initiated.

Exit: no unexplained divergence from the existing workflow and full recovery
after daemon/process restart.

## Phase 5 — guarded review orchestration

Deliverables:

- Exact-commit review packets and diff digests.
- Independent reviewer routing by risk policy.
- Structured findings and candidate follow-up proposals.
- Revalidation after review fixes.
- Stop/carve outcomes and decisions inbox integration.

Merge remains manual. The scheduler may declare `MERGE_READY` but cannot merge.

Exit: reviewed commit, merge candidate, and gate evidence are provably bound.

## Phase 6 — dstdns adapter and host resources

Deliverables:

- Canonical test-runner gate.
- `none`, `readonly`, and `exclusive` stack resource policies represented as
  leases rather than transient marker files.
- Single-stack invariant and recovery evidence.
- Browser/pwmcp resource declaration and sanitized evidence.
- Cross-project host resource fairness.

Exit: a bounded dstdns package completes without duplicating a singleton stack
and with canonical gate evidence.

## Phase 7 — optional automated merge

This phase requires a separate user decision. Preconditions:

- several successful pilot waves;
- exact-commit review and post-merge validation provenance;
- tested crash recovery and serialized merge lane;
- protected-branch and human-approval policy decided by risk class;
- rollback/repair procedure; and
- security review of Git credentials and agent influence.

Automatic merge is not an assumed end state. Manual merge may remain the
preferred policy.

## Deferred

- Multi-host distributed scheduler or consensus.
- Cloud-hosted dashboard.
- Model-based status summaries in the dashboard.
- Generic arbitrary command execution.
- Autonomous product prioritization.
- Automatic changes to specs or roadmap without designated authority.
