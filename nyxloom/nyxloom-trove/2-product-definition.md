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
  - Tiers name the task (type x complexity), not the model.
  - A reviewer route is strictly more capable than the implementer route it reviews.
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

