---
kind: north-star
schema_version: 1
---

# nyxloom — north-star

**The problem.** Teams now have capable AI coding agents but no trustworthy way
to run them unattended at scale. The bottleneck is no longer *"can an agent write
code"* — it is *"can you hand a codebase to a fleet of agents and trust what comes
back."* Left alone, agents drift: they improvise around missing context, ship
plausible-but-wrong changes, mark hollow tests green, and lose the thread of what
the project is even trying to become.

**The mission.** nyxloom makes autonomous software development **trustworthy over
long horizons** — a durable, event-sourced control plane that turns a project's
intent into merged, verified work without a human babysitting every step, while
keeping the human in command of direction.

## The strategic contract — what nyxloom guarantees

- **Direction is a first-class, versioned artifact** — a north-star →
  product-definition → roadmap → backlog spine, continuously diffed against the
  code reality. *The gap between intended and actual is the work.*
- **Correctness is fail-closed, not hopeful** — behavioral-contract oracles,
  BLOCKED-as-success, verdict-verify against git (never a receipt), no dead-end
  states.
- **The human owns direction; the machine owns execution** — through a live
  control surface (dashboard + chat), nyxloom surfaces gaps, escalations, and
  decisions, and a human approves the *what* and the *why*. Silence is never
  consent: a stuck or ambiguous call escalates to a human, it is never guessed.
- **Any project maturity** — empty, mature-but-undocumented, or fully specified.
- **Self-hosting** — nyxloom is built by nyxloom, so every weakness is felt first
  by its own maintainers.

## The technical bets — how it is actually built, the ideas we won't compromise

- **Event-sourced reconciler.** A durable append-only event log is the source of
  truth; state is derived and every transition is validated *before* it is
  appended. The daemon is a Kubernetes-style reconciliation loop — declare desired
  state, converge, repeat — not a script of imperative steps.
- **A files-first control plane.** Direction, handoffs, and reports are managed
  markdown with schema-validated frontmatter in a visible trove. The machine
  trusts *only* the frontmatter; humans read the prose. A non-AI structural
  validator catches corruption or third-party edits fail-closed — never a silent
  mystery.
- **Isolated, real-gated, git-verified dispatch.** Every work package runs in its
  own git worktree, gated by the project's *real* test gate (never a cockpit
  venv), and a reviewer verifies the actual git state — because a self-reported
  receipt has lied.
- **Live, multi-tenant development environments (ciu).** Parallel work in isolated
  worktrees only pays off if each can *run and be tested against a live stack*.
  nyxloom orchestrates ciu to provision per-branch stacks — fully isolated, or
  partially sharing services where safe — so a fleet of agents develops and gates
  in parallel without colliding. Each handoff declares its environment as a
  mechanical bring-up/teardown recipe, so stacks are provisioned to *stated
  requirements*, never improvised.
- **A self-contained, sandboxed agent runtime.** nyxloom ships and version-manages
  the agent CLIs in cgroup-protected containers, with the managed repos mounted in
  and its own credentials held internally — so a run is reproducible and the host
  is protected from a misbehaving agent, never dependent on a hand-configured
  environment.
- **The carver is also a scheduler.** It decides not just *what* to build but *how
  to run it*: grouping work by shared context and shared stack, and choosing serial
  (implement→merge→implement) versus parallel-then-batch-review by conflict risk
  and overlap — turning a backlog into a wave plan across isolated or shared
  landscapes.
- **Capability-matched, cost-aware routing.** Tiers name the *task* (type ×
  complexity), not the model; routes are chosen by availability, per-project
  policy, and a configurable cost posture (prepaid plans first, then cheapest
  viable). A reviewer is always *strictly more capable than the implementer it
  checks*. Under-provisioning surfaces as BLOCKED — a trigger-based escape hatch,
  because models are demonstrably poor at knowing what they missed — and escalates
  up a tier. Product calls become decisions, not improvisation.
- **A no-dead-ends task lifecycle.** Carve → queue → implement → review → merge →
  validate → complete, with explicit reject / blocked / superseded legs; anything
  stuck progresses or escalates, never silently drops.
- **Self-correction as a first-class subsystem.** A runaway watchdog detects
  notification-storms and infinite-retry loops; escalations are windowed and
  de-duplicated; rejects re-queue; merges are validated post-hoc. Production safety
  is designed in, not bolted on.
- **Borrow the plumbing, keep the moat.** Commodity mechanisms — durable waits,
  signals, N-candidate verification, and deploy tooling like ciu — are adopted as
  invariants in nyxloom's *own* event log and orchestration, never by renting an
  engine that would own the strategic layer.
- **Behavioral testing over hopeful mocks.** A scriptable fake-agent harness
  simulates real-world CLI behavior (partial output, parking, malformed replies)
  so state bugs and missing-situation handling are caught before production, not
  after.

## The invariant standard

The north-star does not change with each feature. It is what every roadmap, carve,
and review is measured against: *did this make autonomous development more
trustworthy — or did it just make it faster?*

