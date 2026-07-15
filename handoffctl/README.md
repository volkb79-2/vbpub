# handoffctl

`handoffctl` is a proposed, multi-project control plane for role-separated
AI-assisted software development. It turns handoffs, dependency graphs, gates,
resource ownership, attempts, evidence, and cost into durable machine state.
Models remain responsible for the work that needs judgment: contract design,
implementation, and independent review.

> **Status: design / pilot.** This directory currently contains architecture,
> specification, migration, roadmap, and draft schemas. It does **not** contain
> a scheduler, daemon, dashboard, CLI adapters, or an automated merge path.

## Design goals

- Spend no model tokens on process supervision, polling, status rendering, or
  dependency scheduling.
- Use inexpensive implementation agents for bounded work while preserving an
  independent, risk-appropriate review gate.
- Give every claim a durable provenance chain: contract, exact commits, gate
  environment, result, and evidence artifacts.
- Coordinate several repositories without copying volatile routing or runtime
  state into each repository's instruction files.
- Stop safely when a milestone is complete or when specifications, decisions,
  external dependencies, or budgets prevent useful implementation work.
- Offer a read-only live dashboard without invoking an AI service.

## Proposed components

| Component | Responsibility | Uses AI? |
| --- | --- | --- |
| `handoffd` | Durable state machine, DAG scheduler, leases, subprocess supervision | No |
| `handoffctl` | Operator CLI and drift/audit tooling | No |
| Project adapter | Repository policy, gates, worktrees, project resources | No |
| Agent adapters | Claude, Codex, OpenCode, and Reasonix process/result normalization | Only the launched agent does |
| Evidence runner | Exact-commit validation in the canonical gate environment | No |
| Dashboard | SQLite projection, HTTP API, SSE log/state stream | No |
| Frontier roles | Contract carving, independent review, product-decision preparation | Yes |

## Documents

- [Architecture](docs/ARCHITECTURE.md) defines the planes, components, data
  ownership, security boundary, and dashboard.
- [Specification](docs/SPEC.md) defines normative state transitions, invariants,
  stop outcomes, and adapter contracts.
- [Roadmap](docs/ROADMAP.md) describes the staged design-to-pilot path.
- [Migration](docs/MIGRATION.md) describes read-only import, drift detection,
  shadow operation, and adoption by existing projects.
- [`schemas/`](schemas/) contains draft JSON Schemas for project, handoff, and
  event records.

## Intended repository integration

A consumer repository will eventually keep product truth in its normal specs,
roadmap, decisions, and human-readable handoffs, plus a small machine-readable
integration directory:

```text
.handoffctl/
  project.json
  resources.json        # later schema
  handoffs/
    <task>.json
AGENTS.md
docs/
  ROADMAP.md
  DECISIONS-INBOX.md
  handoff/
```

Runtime state is host state, not product source. It belongs under an XDG state
directory (proposed default: `$XDG_STATE_HOME/handoffctl/`) and is projected
into the dashboard from SQLite. Repositories retain only portable contracts and
selected durable evidence required by their own documentation policy.

## Non-goals for the pilot

- Claiming that an automated process can guarantee semantic correctness.
- Making product decisions without the user-designated authority.
- Replacing project specifications or test suites.
- Automatically merging changes before exact-commit provenance and validation
  have been demonstrated in shadow and pilot operation.
- Using Claude Remote Control, Channels, or any model as the scheduler.
