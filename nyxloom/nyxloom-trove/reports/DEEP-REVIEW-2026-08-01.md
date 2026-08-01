# nyxloom deep product and architecture review

Date: 2026-08-01  
Scope: north-star alignment, product decisions, architecture, state/workflow flexibility,
cost-aware limited-model operation, failure diagnosis/escalation, code quality, tests,
maintainability, and roadmap priorities.

## Executive verdict

nyxloom has a strong, unusually evidence-driven trust core. Its best decisions are the
ones that remove models from mechanical control: a pure reconciliation planner, validated
state transitions, append-only events, real worktrees, fail-safe review verdict binding,
real project gates, canary verification, changed-line coverage, mutation checks, and a
human-owned direction spine. Those choices directly serve the north star.

The current implementation is not yet the cost-aware capability-escalating factory the
north star describes. It is a robust execution substrate with several partially-built
strategic layers. The largest gap is not benchmark ingestion or UI: it is that an
underpowered implementer does not reliably and cheaply escalate to a stronger route.
`BLOCKED` hard-blocks; ordinary errors tend to select the same first healthy route again;
automatic tier promotion depends on first producing reviewable code and then on a reviewer
self-labeling the rejection `incapable`. That is too late, too subjective, and more
expensive than necessary.

The other central issue is change cost. Existing stage lists are configurable, but the
runtime remains a declarative shell around two very large imperative functions. Adding a
new stage or outcome still requires coordinated edits across the state graph, stage
registry, planner, executor, storage projection, rendering, schemas, prompts, and tests.
The deliberate refusal to build an arbitrary workflow DSL is sound; the implementation
still needs a thinner code-backed rule/handler engine.

Overall assessment: preserve the invariants and simplify the mechanism. Prioritize
capability escalation, stale-premise admission, fail-closed audits, authoritative-doc
cleanup, and decomposition before expanding the UI/runtime surface.

## Scorecard

| Area | Assessment | Summary |
| --- | --- | --- |
| North-star coherence | Strong | The mission and invariants are specific, testable, and mostly reflected in the core. |
| Correctness architecture | Strong | Pure planning, validated transitions, evidence binding, and real gates are excellent foundations. |
| Failure visibility | Mixed | Rich events/traces exist, but broad exception fallbacks and text/regex classification can erase causes. |
| Incapability escalation | Weak today | Reviewer-mediated `incapable` exists, but implementer `BLOCKED` and repeated errors do not form a dependable tier ladder. |
| Cost effectiveness | Promising, incomplete | Wave batching, session reuse, cheap-first ordering, and diff-scoped gates help; route selection is still “first healthy.” |
| Workflow flexibility | Moderate for knobs, weak for behavior | Existing stages can be composed safely; new stage kinds/outcomes remain cross-cutting changes. |
| DRY | Mixed | Several good shared helpers exist, but policy/control semantics are repeated across prompts, planner branches, executor branches, docs, and projections. |
| Maintainability | Weakening | `daemon.py` is 8,300 lines; `_execute` is about 1,060 lines; `plan_project` is about 1,160 lines. |
| Reusability | Moderate | Project configs, gates, adapters, and stages are reusable; host assumptions and dual stores increase integration cost. |
| Test strategy | Strong but expensive | Behavioral/property/gate tests are unusually good; giant implementation-coupled suites and one known strict xfail remain. |
| Product truth/docs | Weak | Declared reference docs materially contradict the current trove, storage, merge policy, and implementation status. |

## What the project gets right

### 1. Trust is designed as independent layers

The combination of deterministic planner, isolated attempt, git verification,
independent review, pre-merge gate, post-merge validation, and guarded recovery is much
stronger than relying on any one model or test suite. This matches the north-star rule:
trustworthiness before speed.

Particularly good mechanisms:

- Task and attempt transition graphs are centralized and illegal transitions are rejected
  before append.
- `reconcile.plan_project` is pure and receives an explicit input snapshot, which makes
  property testing and event replay possible.
- Review verdicts are bound to a specific attempt and committed artifact, and missing or
  ambiguous verdicts reject rather than approve.
- Work happens in isolated branches/worktrees; unattended merge uses a real three-way
  merge and gates the merged tree before publishing.
- Changed-line coverage and diff-scoped mutation spend verification effort on the change,
  not on healing the whole history.
- Gate canaries and transport probes test that the gate can actually reject, addressing a
  class of false-green infrastructure failures most agent systems ignore.

### 2. Models are removed from routine supervision

The resident reconciler, wrapper, leases, state projection, rendering, and notifications
are deterministic. This is the correct cost architecture: tokens are reserved for carving,
implementation, diagnosis, and review rather than polling and bookkeeping.

### 3. Cost-saving mechanisms preserve important safety properties

Wave review amortizes the large reviewer startup context across several related diffs.
Warm reviewer sessions keep attempt-bound verdicts, and self-review happens before the
independent review. Prompt additions are argv-bounded. These are good examples of optimizing
cost without silently weakening correctness.

### 4. The project learns from incidents

The doctrine, canary tests, property tests, behavioral fake-agent harness, and historical
rationale show a healthy habit: incidents become mechanisms and regression tests. The
danger is that the same history now lives in too many code comments and overlapping design
documents; the learning loop is right, while its storage needs consolidation.

## Highest-risk gaps

### P0 — Capability escalation does not close the cheap-model loop

Current behavior:

1. A handoff selects a task tier.
2. Dispatch chooses the first healthy route in that tier.
3. Provider transient/limit signals can pause a route and cause another selection.
4. An implementer `BLOCKED` becomes `TASK_BLOCKED` with a contract blocker.
5. A normal non-provider error requeues while budget remains, usually selecting the same
   first healthy route.
6. `incapable` promotion occurs only after a reviewable artifact is independently rejected
   and the reviewer writes `REJECT_CLASS: incapable`.
7. That signal routes through a carver turn to produce a fresh handoff at a higher tier,
   even though the scope is explicitly supposed to remain the same.

This contradicts the north-star statement that under-provisioning surfaces as `BLOCKED`
and escalates up a tier. The implementation has a reviewer-rejection escalation path, not a
general capability escalation path.

Recommended design:

- Add a typed `FailureKind`: `provider_transient`, `provider_limit`, `environment`,
  `contract_missing`, `decision_required`, `scope_required`, `implementation_defect`, and
  `capability_suspected`.
- Keep raw agent markers as evidence, never sole authority. Combine them with deterministic
  signals: repeated same-route failures, no meaningful worktree progress, repeated oracle
  misses, or a stronger diagnosis verdict.
- Track `requested_tier` and `effective_tier` separately. Promotion should append a typed
  event and dispatch the same handoff on the next band; it should not rewrite the handoff.
- Bypass a new carver turn when scope is sound. A carver is appropriate for architectural
  or stale-premise re-scoping, not for a mechanical tier bump.
- Use a bounded ladder: same-session transient resume; next route in band; next band;
  independent diagnosis; human. Every step needs an explicit cap and reason.
- Record predicted band versus minimum band that succeeded, first-pass acceptance, total
  accepted-task cost, retries, and escalation cause. Use this local evidence to calibrate
  future carving; benchmarks should seed priors, not dictate routing.

Acceptance proof: a fake band-1 agent that emits a capability-shaped failure must reach a
band-2 route with the same scope and no human/carver turn; an environment/contract failure
must not be mispromoted; the ladder must terminate at a human decision after its cap.

### P0 — Carved premises can be stale at first dispatch

`input_revision` is checked during rejection triage and proposal admission, but an ordinary
queued handoff is not revalidated against current `main` immediately before a fresh
implementer dispatch. This is already recorded as backlog B12. Restoring
`carve_ahead_target = 10` before fixing it increases the chance that a cheap model faithfully
implements obsolete assumptions.

Recommendation: make premise validity part of the single execute-time admission token.
Before every fresh attempt, prove that the handoff revision is still an ancestor of current
main and that declared context hashes, if present, still match. Route stale work to the
carver with a typed drift report. Do not restore carve-ahead until this is green.

### P0 — Declared product references are not authoritative in practice

The `[refs]` docs are declared as material nyxloom reads, but several are old draft/pilot
documents:

- `docs/ARCHITECTURE.md` still describes old `docs/.../handoff`,
  `.nyxloom/project.toml`, and host-XDG runtime layout.
- `docs/SPEC.md` says automatic merge is disabled, while self-host config uses
  guarded automatic merge.
- `docs/ROADMAP.md` says no milestone is complete; the trove roadmap says M1/M2 are done.
- `docs/EVOLUTION.md` still describes a design/pilot migration and manual merge.
- The README still frames the product as draft 2 and says no automated merge.

This is not cosmetic. Carvers and reviewers are explicitly told to read declared refs; stale
references inject contradictory product truth and waste limited-model context.

Recommendation: establish one authority per concern. Mark historical docs `superseded` and
remove them from `[refs]`, or update them to current architecture. Add schema metadata such
as `lifecycle = current|superseded|historical` and lint that `[refs]` may name only current
documents. Add contradiction checks for a small set of machine-known facts: state backend,
trove path, merge mode, active milestones, and daemon mode.

### P0 — Fail-open exception handling remains a systemic risk

The review counted 84 `except Exception` sites in `daemon.py`. Many protect optional
observability and are appropriate; others derive facts used to authorize work. Before this
review, a failed lease probe returned “free,” decision-inbox load failure became “no open
decisions,” and lint-all silently ignored a project whose config could not load. Those three
are fixed, but they show the category needs a deliberate audit.

Rule: failures in authority, exclusivity, config, decisions, git identity, event history,
or evidence must stop/park the relevant action. Failures in rendering, optional
notifications, or supplemental diagnostics may degrade. Encode this distinction in helper
names/types rather than relying on local judgment.

Acceptance proof: fault injection over every snapshot builder labels each dependency
`authoritative` or `advisory` and asserts the former causes zero launch/merge effects when it
throws.

## Architecture and maintainability

### The state machine is safe but not easy to change

The “stages as data, no arbitrary flow language” decision is good. Arbitrary user-defined
states and conditional expressions would make the invariant surface unreviewable.

Current flexibility is narrower than the config suggests:

| Change | Current difficulty | Why |
| --- | --- | --- |
| Reorder/omit an existing compatible stage | Low | `pipeline` composition and closure validation handle it. |
| Change per-stage concurrency | Low | Registry defaults plus project overrides are centralized. |
| Change prompt/context policy | Medium | Multiple cold/resume/diagnosis builders and argv budgets must stay aligned. |
| Add a new outcome to an existing stage | High | Registry metadata, planner branches, executor consumption, parsing, event payloads, and tests all change. |
| Add a stage kind | Very high | Planner and the 1,060-line `_execute` interpreter require new hardcoded paths. |
| Add a state | Very high | Enum, graph, schemas, projections, planner, executor, render legend, invariants, resync, and migration all change. |

`STAGE_REGISTRY` validates metadata, but it does not drive most runtime behavior. The real
workflow is still encoded in `plan_project` and `Daemon._execute`. This is a declarative
description beside an imperative engine, not yet a compiled workflow.

Recommended target without a DSL:

1. Keep `TaskState`, legal transition graph, event append, storage projection, and admission
   hard-coded as the trusted kernel.
2. Define code-backed `StageHandler`/`ActionHandler` records containing stage metadata,
   a pure planning function, an effect handler, evidence schema, and retry policy.
3. Compile the configured pipeline at load into an immutable execution plan and validate
   ownership/closure once.
4. Dispatch actions through a handler map instead of the giant `isinstance` chain.
5. Split effectors by bounded concern: attempts, reviews, carver, gates, merge, and operator
   control. Keep `Daemon` as orchestration only.
6. Generate the state legend and supported-action matrix from the same registry so UI and
   runtime cannot drift.

This preserves the fixed invariant kernel while making new code-backed stages local changes.

### Monolith metrics

- `daemon.py`: 8,300 lines, 158 functions/methods, fan-out to 25 nyxloom modules.
- `reconcile.py`: 2,302 lines; `plan_project` is roughly 1,160 lines.
- `render.py`: 2,526 lines.
- `cli.py`: 2,103 lines.
- `Daemon._execute`: roughly 1,060 lines.
- `adapters.build_dispatch`: roughly 541 lines.
- The production import graph is mostly acyclic; the notable cycle is
  `storage <-> storage_sqlite`.

These sizes do not make the code wrong, but they make semantic review expensive for limited
models and amplify regression risk. A model must hold too much history and too many adjacent
branches to make a small workflow change safely.

### Code comments are becoming a second event log

The rationale attached to failures is valuable. Many functions now carry package IDs,
dates, old behavior, refinements, and superseding refinements inline. That makes the current
contract difficult to see and duplicates LESSONS/decisions/design docs.

Recommendation: keep a concise current contract and invariant in code; move incident
narrative to an ADR/lesson linked by stable ID. Enforce a soft limit for function and
docstring size. The goal is not fewer explanations; it is one current explanation plus one
durable history.

## DRY and reuse findings

Good reuse already present:

- Central transition validators and serde.
- Shared attempt-budget helpers replacing earlier role-blind formulas.
- Central stage concurrency resolution and pipeline closure validation.
- Shared gate runner/canary infrastructure.
- Reusable project config, route adapters, and gate declarations.

Important duplication/drift surfaces:

- Reject taxonomy existed separately in cold and warm reviewer prompts. The warm path
  omitted `incapable`; both omitted/handled `transient` inconsistently. This review replaced
  them with one shared instruction and a regression test.
- “First healthy route” loops are repeated and separate from reviewer/carver route selection.
- State behavior is described in the transition graph, stage exit maps, planner branches,
  executor branches, render legend, schemas, and docs.
- File and SQLite storage backends duplicate the event/state contract and create an import
  cycle.
- Current trove direction and old draft refs duplicate product/architecture truth.

## Routing and cost design review

### What works now

- Tiers name work rather than providers.
- Ordered routes provide a simple, predictable fallback.
- Health probes and temporary provider pauses can skip a broken route.
- Wave batching and session reuse reduce repeated context cost.
- Usage and price data can be captured, with explicit actual/estimated/unknown basis.
- Free endpoints are opt-in and receive a prompt guard.

### What is not wired into dispatch

- The capability catalog does not enforce route selection.
- `RouteDef.status` such as `fallback-only` is not consulted by normal selection.
- Cost posture, per-project data policy, provider/model disablement, reserve budgets, and
  strict reviewer capability are plans, not current dispatch behavior.
- Reviewer/carver sites repeatedly choose the first role route.
- Provider pauses live in daemon memory. Although `PROVIDER_STATE_CHANGED` is persisted, a
  daemon restart does not rebuild the pause deadline, so a bad route becomes eligible early.
- “Reviewer strictly more capable” is stated but not mechanically proven against the actual
  selected implementation route.

### Suggested route selector

Create one pure `select_route(request, snapshot) -> Selection | NoRoute` component. Inputs:

- task verb/band and actual prior route attempts;
- required context, sandbox, privacy, tool, and environment capabilities;
- operator hard exclusions and soft preferences;
- provider health/disablement/reserve state;
- estimated tokens and cache-reuse probability;
- catalog capability with confidence/freshness;
- local route reliability and acceptance history for the task archetype.

Return the winner, ordered runners-up, filtered reasons, expected cost range, and confidence.
Persist that explanation on the attempt. All implement/review/carve/diagnosis sites must use
this one selector.

Question the “strictly stronger reviewer” rule. The real requirement is sufficient,
independent reviewing capability plus a real mechanical gate. A strict per-axis dominance
rule can force an expensive model even when a same-capability model with independent context
and a stronger test gate has better expected value. Make strict dominance a high-risk policy,
not necessarily a universal invariant; validate it empirically with escape-rate data.

## Storage and event sourcing

The event-first design is appropriate. The file backend has a known append-then-projection
crash window healed by replay; SQLite can make event and projection atomic. Maintaining both
indefinitely doubles semantic tests and complicates every storage change.

Recommendation:

- Keep markdown/trove files as human product truth.
- Make SQLite WAL the single runtime event/projection store in the persistent volume after a
  measured migration/shadow period.
- Retain an append-only JSONL export command for greppability and disaster recovery rather
  than a second live backend.
- Extract pure event validation/projection from `storage.py` so the backend does not import
  back through its facade.
- Add event schema upcasters and explicit projection versioning before v1 format stability.

## Test and gate review

Strengths:

- Large unit, integration, behavioral, property, crash, canary, coverage, and mutation suites.
- Tests often state negative cases and historical failure mechanisms.
- Dedicated Python 3.14 gate container has complete uid/group/HOME/XDG identity.
- Gate execution is detached and transport trust is probed.

Concerns:

- `test_daemon.py` is over 7,000 lines and mirrors implementation structure heavily.
- Snapshot/source-scan tests can preserve text without proving user-visible behavior.
- One strict xfail documents reachable `DRAFT` as a dead state but explicitly says no backlog
  item tracks it. Either remove `DRAFT` from the live domain or give it a real owner/transition.
- `requires-python >=3.11` is broader than the only authoritative 3.14 gate. Either test the
  supported minimum or declare 3.14.
- Runtime dependency ranges and a `latest` base image weaken the reproducibility claim.

Recommended layers:

1. Fast kernel suite: transitions, projections, pipeline compiler, route selector.
2. Component contract suites: one per action handler/effect boundary.
3. A compact set of end-to-end journeys with real fake CLI, git, wrapper, and gate.
4. Fault-injection matrix for authoritative dependencies.
5. Changed-line coverage and mutation only on the changed bounded component.
6. Scheduled full/chaos/replay suite outside every small inner-loop change.

## Product-definition and roadmap review

The feature statuses are too coarse to be operational truth. F007, F008, F009, and F018 are
marked planned while meaningful portions exist in code; F011/F012 are building; individual
acceptance criteria within each feature vary substantially. “Planned” hides partial assets
and encourages duplicate work, while “shipped” can overclaim a subset.

Add per-acceptance evidence:

```yaml
- criterion: "..."
  status: absent|building|proven|degraded
  evidence:
    - test: tests/test_x.py::test_behavior
    - code: src/nyxloom/x.py
    - observed_at: 2026-08-01
```

The gap engine should compare criteria and evidence, not infer a whole feature from one
status field. Evidence should expire when its referenced path/test disappears or when a
declared invariant changes.

## Recommended delivery order

### Now — before unfreezing autonomous carving

1. **DR-01 Capability ladder.** Implement typed failure kinds, effective-tier promotion,
   route-history avoidance, caps, and behavioral matrix tests.
2. **DR-02 Stale-premise admission (B12).** Revalidate at every fresh dispatch effect
   boundary; keep carve-ahead at zero until proven.
3. **DR-03 Fail-open audit.** Classify all broad exception handlers as authoritative or
   advisory; fault-test every authoritative snapshot input.
4. **DR-04 Product truth cleanup.** Reconcile/remove stale `[refs]`; make current/superseded
   lifecycle lintable.
5. **DR-05 Route selection seam.** Replace every first-route loop with one pure selector,
   initially preserving current ordering exactly.

### Next — reduce change cost before adding more workflows

6. **DR-06 Action-handler extraction.** Split attempts, reviews, carver, gate, and merge
   effectors out of `Daemon._execute`, with parity tests.
7. **DR-07 Planner rule extraction.** Split `plan_project` into ordered pure rules operating
   on a shared plan context; keep ordering explicit and property-tested.
8. **DR-08 Compile stages to handlers.** Make registry metadata drive planning/execution
   dispatch and generate supported-state matrices.
9. **DR-09 Runtime store convergence.** Shadow SQLite, compare projections, switch default,
   keep JSONL export, then remove the dual live backend.
10. **DR-10 Durable provider/route health.** Rehydrate disable/pause state from events with
    expiry, reason, and operator override.

### Then — complete the strategic product layer

11. **DR-11 Cost/capability selector.** Wire catalog, policy, price, cache estimate, local
    reliability, and capability confidence into selection.
12. **DR-12 Criterion-level reality evidence.** Give product-definition acceptance items
    evidence/status and drive gap audits from them.
13. **DR-13 Typed review result.** Replace regex-only report parsing with a schema-validated
    review artifact bound to task, attempt, head SHA, and diff digest; retain markdown as the
    human rendering.
14. **DR-14 Runtime sandbox.** Finish F010 before using untrusted/free routes broadly; a
    prompt telling an endpoint not to expose secrets is not a security boundary.
15. **DR-15 Human control and explanations.** Surface why a route/action was selected, which
    alternatives were filtered, failure fingerprint, escalation ladder position, and what
    exact user decision can unblock it.

## Out-of-the-box opportunities

### Counterfactual event replay lab

Because planner inputs and events are durable, replay historical projects under a proposed
policy before deploying it: “What would cost-min have selected?”, “Would this new retry rule
have looped?”, “How many human escalations would strict reviewer dominance have caused?”
This turns workflow changes from intuition into measured simulations.

### Minimum-sufficient-model learning

Treat each completed attempt as weak supervision for the minimum sufficient band. Use task
features already available at carve time (scope breadth, files/components, oracle count,
dependency fan-out, novelty, context estimate) and update a conservative per-project model.
Use credible intervals and explore only within budget; never silently downgrade a high-risk
task. This local calibration is more relevant than global benchmark rank.

### Failure fingerprints and remedy memory

Normalize errors into fingerprints over phase, exit, exception type, gate tail hash, changed
files, and route. Link a fingerprint to the remedy that previously worked: resume, provider
switch, tier bump, re-carve, environment rebuild, or decision. This reduces repeated diagnosis
turns while keeping the deterministic controller in charge.

### Workflow model checker

Generate the stage/state/action matrix and inject every outcome at every legal boundary:
daemon crash, missing receipt, stale verdict, bad event append, provider restart, gate timeout,
merge conflict, unreadable decision file. Assert every reachable state has progress, a durable
wait, or a bounded escalation. This is a better flexibility oracle than source scans.

### Shadow planner and two-phase workflow rollout

Run a new planner/routing policy in shadow against live snapshots, persist proposed actions,
and compare them with the active planner. Promote only after no unexplained differences over a
representative window. This mirrors the product's own gate philosophy for its control logic.

### Human-attention budget

Optimize not only money/tokens but scarce operator interruptions. Give every escalation an
urgency, decision deadline, expected cost of waiting, reversible default (when allowed), and
dedupe key. Measure avoidable escalations and time-to-answer alongside token spend.

### Evidence freshness graph

Bind claims in the product definition, docs, reviews, and gate declarations to code/test
artifacts. When a referenced artifact changes, mark the evidence stale and schedule a cheap
mechanical check before an AI audit. This makes “intent versus reality” incremental.

## Direct improvements made in this review

1. Unified the reviewer reject taxonomy into one shared instruction used by cold and warm
   review paths.
2. Added `incapable` and `transient` to both paths so session reuse cannot suppress
   capability or infrastructure routing signals.
3. Changed lease availability probing to fail closed on inspection errors and emit a bounded
   warning.
4. Changed decision-inbox snapshot loading to abort the reconcile pass on read/parse failure
   rather than treating every decision as resolved.
5. Changed lint-all to emit `L0 error` and exit non-zero when a registered project's config
   cannot load, rather than silently ignoring the project.
6. Added focused regression tests for each behavior.

## Interview questions for the operator

1. Should a task that says `BLOCKED` ever auto-promote, or do you want only a stronger
   independent diagnosis to authorize capability escalation? Recommended: typed blockers
   auto-route obvious provider/scope/decision cases; repeated unexplained agent failures get
   one bounded stronger diagnosis, then deterministic promotion.
2. Is “reviewer strictly more capable than implementer” a non-negotiable safety invariant,
   or may policy choose an equally capable but independent reviewer when the gate is strong
   and measured escape risk is low? Recommended: risk-based policy, strict dominance for
   high-risk/weak-gate tasks.
3. How often do you expect projects to add genuinely new stage kinds or states, versus only
   composing the current stage menu? This determines whether handler extraction is enough or
   a versioned plugin API is justified.
4. Are you comfortable making SQLite the only live runtime store after a shadow migration,
   with JSONL as export/audit format? Recommended: yes; files-first should describe human
   product artifacts, not force a non-atomic runtime database.
5. Which optimization is primary: lowest token/API cost, maximum use of prepaid plans,
   shortest wall-clock, or minimum human interruptions? The selector can optimize a weighted
   objective, but one must be the tie-break authority.
6. May nyxloom learn per-project routing policy from historical outcomes automatically, or
   should it only recommend changes for operator approval? Recommended: learn scores
   automatically, require approval for policy/band threshold changes until calibrated.
7. Do you want the next phase to harden the current trustworthy core before F010-F015 UI and
   runtime expansion? Recommended: finish DR-01 through DR-10 first; otherwise new surfaces
   will bind more code to the current monolith and incomplete escalation model.
