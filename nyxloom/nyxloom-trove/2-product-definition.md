---
kind: product-definition
schema_version: 1
product_version: 1
features:
- id: F001
  title: Direction spine
  acceptance:
  - The four spine docs (north-star/product-definition/roadmap/backlog) exist as managed
    markdown with schema-validated frontmatter.
  - nyxloom lint S1-S5 flags a corrupt, schema-invalid, or duplicate-id spine doc
    as a hard error (fail-closed), never a silent skip.
  - The frontmatter is the machine-trusted surface; the markdown body is human narrative
    the machine never parses for correctness.
  status: shipped
  milestone: M1
- id: F002
  title: Guided onboarding (any maturity)
  acceptance:
  - A non-AI wizard records project maturity, mode, and scan-paths without an agent.
  - A read-only assessment scan produces a structured AssessmentResult, or short-circuits
    for a greenfield (empty) repo without dispatching.
  - The questionnaire drafts a lint-green spine north-star-first, and fails closed
    (no partial or invalid spine written) on an unparseable or internally-inconsistent
    draft.
  status: shipped
  milestone: M2
- id: F003
  title: Event-sourced task lifecycle
  acceptance:
  - Every task moves through carve -> queue -> implement -> review -> merge -> validate
    -> complete, with explicit reject / blocked / superseded legs.
  - 'No state is a dead end: a stuck task always progresses or escalates.'
  - An illegal state transition is rejected before it is appended to the event log.
  status: shipped
  milestone: M1
- id: F004
  title: Isolated, real-gated, git-verified dispatch
  acceptance:
  - Each work package runs in its own git worktree.
  - The gate is the project's real declared gate, never the cockpit/devcontainer venv.
  - A reviewer verifies the actual git state (log/status/diff of the branch), not
    a self-reported receipt.
  status: shipped
  milestone: M1
- id: F005
  title: Fail-closed correctness contract
  acceptance:
  - Each handoff carries non-hollow oracles, each with an observable and a negative.
  - An agent that cannot meet the contract emits BLOCKED and is re-routed up, rather
    than silently improvising a workaround.
  - A rejected review re-queues the task rather than dropping it.
  status: shipped
  milestone: M1
- id: F006
  title: Self-correction subsystem
  acceptance:
  - A watchdog detects notification-storms and infinite-retry loops.
  - Rejection-driven escalations are windowed and de-duplicated so a single area cannot
    storm the notification channel.
  - A merged change is validated post-hoc (MERGED -> VALIDATING -> COMPLETED, or BLOCKED
    on failure).
  status: shipped
  milestone: M1
- id: F007
  title: Gap-engine (intent<->reality diff)
  acceptance:
  - The product-definition features are diffed against the code reality.
  - Detected gaps are surfaced as carve candidates, replacing ad-hoc carve inference.
  - A feature marked shipped whose implementing code or tests are absent is flagged.
  status: planned
  milestone: M3
- id: F008
  title: Carver-as-scheduler
  acceptance:
  - The carver estimates each task's complexity and assigns an implementation tier.
  - Work is grouped into waves by shared context and shared stack, capped at five
    per wave.
  - The carver chooses serial (implement->merge->implement) versus parallel-then-batch-review
    by conflict risk and overlap.
  status: planned
  milestone: M4
- id: F009
  title: Capability-matched, cost-aware routing
  acceptance:
  - Tier keys are verb-band (implement/review/carve x 1-3), never a model or provider
    name; the model becomes a route selected at dispatch.
  - A reviewer route is strictly more capable than the implementer route it reviews,
    compared per-axis (implementer on the coding axis, reviewer/carver on the
    reasoning/agentic axis) against the F014 capability catalog.
  - A disabled CLI, provider, or model is skipped during route selection without removing
    its config.
  - Route selection honors a configurable cost posture and per-project route policy.
  status: planned
  milestone: M4
- id: F010
  title: Self-contained, sandboxed agent runtime
  acceptance:
  - Agent CLIs run in cgroup-protected containers managed by ciu.
  - The managed repos/worktrees are mounted into the CLI containers.
  - nyxloom holds its own credentials and a run is reproducible without host-preconfigured
    CLIs.
  status: planned
  milestone: M5
- id: F011
  title: Live, multi-tenant development environments (ciu)
  acceptance:
  - Each handoff declares its environment as a mechanical bring-up/teardown recipe.
  - ciu provisions per-branch stacks that are fully isolated or partially share services
    where safe.
  - Parallel worktrees each gate against their stack without collision.
  status: building
  milestone: M5
- id: F012
  title: Human control surface
  acceptance:
  - A dashboard surfaces task state, gaps, escalations, and pending decisions.
  - A human can steer direction and answer escalations through chat.
  - An ambiguous or stuck call escalates to a human and is never silently guessed.
  - A read-only routing panel surfaces the capability catalog and, per tier, the resolved
    route with runners-up and the filters (policy, availability, cost posture) that fired.
  status: building
  milestone: M6
- id: F013
  title: Behavioral test harness
  acceptance:
  - 'A scriptable fake-agent CLI simulates real-world behaviors: partial output, turn-parking,
    malformed replies.'
  - Property and invariant tests assert lifecycle correctness.
  - State-machine bugs and missing-situation handling are caught before production,
    not after.
  status: shipped
  milestone: M1
- id: F014
  title: Model capability catalog
  acceptance:
  - Every discoverable model (free and paid, across providers) is mapped to a per-axis
    capability vector (coding / agentic / reasoning) plus price, context, and privacy,
    from a pluggable registry of benchmark sources.
  - Band membership is decided per-axis by operator-set thresholds, so a newly-discovered
    model bins itself without shifting other models' bands.
  - The complexity band is auto-assigned; review/carve role-eligibility is operator-gated
    unless capability_map.role_gating is set to auto.
  - The catalog is written as a sibling managed block; the frozen config core is never
    edited.
  status: shipped
  milestone: M4
- id: F015
  title: Scheduled-jobs subsystem
  acceptance:
  - The daemon owns scheduled jobs; the capability-catalog refresh is the first consumer.
  - Config-driven jobs are read-only in the UI (config is their source of truth);
    user-driven jobs added through the UI are editable there; neither origin silently
    overwrites the other.
  - Every scheduled job is also invocable ad-hoc without waiting for its next tick.
  status: planned
  milestone: M6
- id: F016
  title: System-to-user findings channel
  acceptance:
  - An advisory insight that is neither a task event nor a blocking decision is recorded as
    a typed FINDING_RECORDED event (event-first), rendered as an html-escaped dashboard card.
  - A pushable finding kind builds its notification from a fixed template over typed fields
    only, never from model-authored free text (the SPEC-13 injection boundary).
  - A finding is actionable, not a dead end - the operator can promote it into an intake
    conversation that flows into the existing decision/carve pipeline.
  - Cost-equivalence findings are auto-emitted from the capability catalog (a cheap model
    matching a stronger one at a fraction of the cost), closing the loop to a routing choice.
  status: shipped
  milestone: M6
- id: F017
  title: Mechanized correctness gates (behavior and pre-merge)
  acceptance:
  - Beyond line-reach coverage, a changed-lines mutation gate proves each changed line's
    behavior is asserted (a surviving mutant is a hollow test), scoped to the diff.
  - The deterministic gate runs BEFORE publish on the merged tree, so an unattended merge
    never leaves the default branch red; a failing pre-merge gate routes back for a fix.
  - The independent-review stage is named for its role, not a model tier; state transitions
    stay mechanical and an LLM only emits typed judgments consumed by the state machine.
  status: building
  milestone: M1
- id: F018
  title: Long-running, harness-portable strategic carver
  acceptance:
  - One resumed carver session per project holds north-star/roadmap/backlog context across
    bounded daemon turns, fed a typed merge-digest after each merge (no repo re-scan).
  - Its output is a schema-checked carve proposal the deterministic planner admits
    mechanically; it does not review its own work.
  - Its durable context is the spine (ground truth) plus a harness-neutral working summary,
    so the runner (claude/codex/reasonix/opencode) and model are a per-turn choice and
    context survives a harness switch; compaction is per-runner (explicit where supported,
    tuned auto-compaction otherwise) and lossy-safe against the spine.
  - It is the entry point for a human's new plan or raw feature idea, fitting it into the
    current machine work-state.
  status: planned
  milestone: M4
non_goals:
- Not a general-purpose CI/CD system.
- Not an interactive IDE / coding-assistant for a human at the keyboard.
- Not a model provider or inference engine (it borrows commodity plumbing).
- Not locked to a single agent CLI or model vendor.
---

# nyxloom — product-definition (v1)

Version 1 is dogfood-honest: it states nyxloom's guarantees as checkable
features AND doubles as its real status. The `shipped` features are the
trustworthy core + guided onboarding; the `planned`/`building` features are
the intent<->reality, scheduling/routing, runtime, and human-control work
ahead. The gap between shipped and planned IS the roadmap (see 3-roadmap.md).

Each feature's `acceptance` is the behavioral contract a carve/review is
measured against. `non_goals` bound what nyxloom deliberately is not.

