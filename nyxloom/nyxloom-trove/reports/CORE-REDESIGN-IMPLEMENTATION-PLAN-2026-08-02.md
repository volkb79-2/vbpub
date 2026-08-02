# nyxloom core redesign implementation plan

Date: 2026-08-02  
Status: proposed for external review  
Source assessment: `DEEP-REVIEW-2026-08-01.md`  
Scope: DR-01 through DR-15. DR-16 and DR-17 are explicitly excluded.

## 1. Objective

Redesign nyxloom's control core before adding more workflow behavior. The resulting system
must preserve the north-star trust properties while making workflow topology, routing,
capability escalation, prompts, and evidence policy changeable through validated
instance-owned definitions rather than coordinated edits across monolithic functions.

The target is not to unfreeze autonomous carving. Success is a smaller, typed, explainable
kernel on which cost-efficient operation—from local/free limited models through frontier
routes—can be implemented without hidden retry loops or model-controlled state.

## 2. Fixed operator decisions

These are inputs to implementation, not open product questions:

1. Workflow configuration is instance-owned in the first release. Managed projects and end
   users do not author workflows or prompt templates yet.
2. The workflow is declarative and compiled. Configuration may compose registered handlers,
   prompts, safe guards, bounded loops, review joins, and human waits. It may not run arbitrary
   code, mutate state directly, or waive kernel invariants.
3. Use five role-specific capability bands. Band 1 spans bounded local/free work; band 5 is
   frontier maximum-reasoning work. Providers/models are routes, not band names.
4. A valid explicit capability decline immediately promotes the unchanged task by one band
   and excludes that route for the task fingerprint. It is not retried on the same model.
5. Independent diagnosis is used for ambiguous failures, conflicting evidence, expensive or
   terminal escalation, and band-5 failure—not before every explicit decline.
6. Review strength is risk-dependent. `implement-N -> review-N` is normal if the selected
   review route is independently eligible for that role/risk. A universal “review model must
   be numerically stronger” rule is rejected.
7. Optimize expected total cost per accepted correct change, including failed attempts,
   review, gates, escalation, and human interruption—not the cheapest first call.
8. SQLite is the sole live runtime store. There is no requirement to preserve a live file
   backend or external storage compatibility. JSONL is export/recovery only.
9. Superseded and historical documents leave active references/context and move to archive.
10. Routing observations update automatically; thresholds and policy changes require operator
    approval and must be explainable back to the jobs that produced the evidence.
11. Core redesign precedes new autonomous behavior. DR-16 local-model work and DR-17 contender
    experiments remain research notes only.

## 3. Verified starting point

The plan is grounded in the current tree, not only design prose:

- `src/nyxloom/daemon.py` is 8,308 lines. `Daemon._execute` begins at line 6,362 and is an
  approximately 1,000-line action interpreter after several thousand lines of effect helpers.
- `src/nyxloom/reconcile.py` is 2,302 lines. `plan_project` begins at line 975 and encodes
  ordering and policy across a large imperative function.
- `src/nyxloom/stages.py` declares seven stage kinds, but its metadata drives only parts of
  planning/execution.
- `src/nyxloom/storage.py` selects between file and SQLite implementations through
  `NYXLOOM_STATE_BACKEND`; `storage_sqlite.py` already has atomic event/projection tests and
  enables SQLite WAL.
- Action dataclasses are concentrated in `reconcile.py`; executor dispatch and event emission
  remain primarily in `daemon.py`.
- Review classification is partly typed in events but still originates in markdown/text
  parsing. Capability decline is not a complete first-class result contract.
- The default registered menu is `carve`, `implement`, `self_review`,
  `review_independent`, `triage`, `auto_merge`, and `post_merge_gate`.
- The real gate is `[gates.tester-unified]` in `nyxloom-trove/nyxloom.toml`; cockpit tests are
  diagnostic only and never release evidence.

## 4. Target architecture

### 4.1 Layering

```text
instance workflow source + route/risk policy + prompt templates
                              |
                              v
                       workflow compiler
                              |
                 immutable ExecutionPlan + digest
                              |
              +---------------+----------------+
              |                                |
       pure planning rules               route selector
              |                                |
              +---------- typed Actions -------+
                              |
                       handler registry
                              |
                 isolated effect handlers
                              |
             typed Results -> kernel validation
                              |
        SQLite event append + projection transaction
                              |
            generated task trace / UI / JSONL export
```

The daemon becomes an orchestration shell: build an authoritative snapshot, invoke the pure
planner, execute typed actions through the registry, validate results, and append events. It
does not contain workflow-specific `isinstance` branches or route-selection loops.

### 4.2 Separate lifecycle from workflow position

The current `TaskState` enum mixes stable lifecycle facts with particular workflow nodes.
That is why adding a stage requires changes to enums, storage, rendering, planning, and tests.

Replace it with two axes:

```text
TaskLifecycle = OPEN | RUNNING | WAITING | TERMINAL
TerminalReason = COMPLETED | CANCELLED | SUPERSEDED | FAILED
WaitReason = DECISION | CONTRACT | NO_ROUTE | BUDGET | OPERATOR | ENVIRONMENT

TaskRuntime:
  workflow_id
  workflow_version
  workflow_digest
  node_id
  node_status        # READY | ACTIVE | WAITING_RESULT
  lifecycle
  wait_reason?
  terminal_reason?
  transition_seq
```

Human-facing labels such as “awaiting independent review” are generated from the compiled
node definition. The kernel validates lifecycle changes; the compiled workflow validates
`node_id --outcome--> next_node`. A new instance-configured review node therefore does not
create a new database enum or unrestricted state transition.

Mandatory kernel rules remain code:

- event validation occurs before append;
- only the active node's registered handler may submit a result;
- results bind to project, task, attempt, role, artifact digest, workflow digest, and prompt
  digest;
- every effect passes admission, budget, lease, and route policy;
- merge requires an artifact-bound independent approval and the configured pre-merge gate;
- terminal tasks cannot re-enter a workflow except through an explicit operator-created new
  task/revision;
- loops consume a declared retry/escalation budget;
- unrecognized/malformed evidence never approves or merges.

### 4.3 Workflow source and compiler

First-release workflow definitions are operator-managed instance configuration, preferably a
versioned file alongside live routes configuration in the nyxloom state/config volume. Ship
example/default definitions with the product. A registered project refers to a workflow name;
the managed repository cannot inject executable handlers or arbitrary expressions.

Minimum source vocabulary:

```yaml
schema: nyxloom.workflow/v1
id: standard
start: carve
nodes:
  implement:
    handler: dispatch_agent
    role: implementer
    prompt: implement/v2
    tier: implement-1
    outcomes:
      completed: self_review
      capability_declined: promote_implement
      contract_missing: decision
  review_code:
    handler: dispatch_agent
    role: review_independent
    join: {mode: all, group: independent_reviews}
    outcomes: {approved: merge, rejected: triage}
  promote_implement:
    handler: promote_capability
    config: {max_band: 5}
    outcomes: {promoted: implement, exhausted: decision}
```

No arbitrary expression evaluator is required. Guards name registered pure predicates over a
typed snapshot, for example `touches_tests`, `touches_security_boundary`, `gate_rigor_below`,
`effective_band_at_least`, or `decision_open`. The compiler resolves and type-checks them.

Compile-time rejection conditions:

1. Unknown handler, role, prompt, guard, outcome, join, or policy field.
2. An outcome not declared by the handler's result type.
3. An edge to an absent node or a lifecycle mutation outside the kernel.
4. An unreachable node or no path from start to terminal/wait.
5. A strongly connected component with no statically bounded retry/escalation counter.
6. Merge reachable without required independent approval and gate evidence.
7. An agent-controlled node able to emit merge, approval-for-self, policy-change, or safety
   exemption directly.
8. Parallel branches without a typed join policy and cancellation behavior.
9. A prompt/handler version missing from the immutable execution-plan digest.

The compiler emits canonical normalized JSON (or equivalent typed structures), its digest,
supported outcome/state matrices, and diagnostic source locations. Runtime never interprets
raw YAML/TOML dynamically after compilation.

### 4.4 Handler contracts

Define a generic registered handler contract while retaining typed handler-specific input and
result models:

```text
HandlerSpec:
  kind
  input_type
  result_type
  declared_outcomes
  planner
  effector
  evidence_schema
  idempotency_key_builder
  required_admission_classes
```

Effect handlers do I/O but do not choose the next node. They return a typed result; the
kernel maps its outcome through the compiled plan and appends the transition atomically.

Initial handler families:

- agent dispatch/resume/interrupt;
- self and independent review;
- gate and gate diagnosis;
- carve/session operations;
- guarded merge and post-merge validation;
- human decision wait/resume;
- capability promotion and bounded retry;
- parallel review join/cancellation;
- operator control.

### 4.5 Typed result envelope

Use one versioned envelope for implement, review, diagnosis, carve, and later local advisory
work. Role-specific payloads remain discriminated unions.

```json
{
  "schema_version": 1,
  "kind": "agent_result",
  "project": "nyxloom",
  "task_id": "...",
  "attempt_id": "...",
  "role": "implementer",
  "outcome": "completed|capability_declined|blocked|failed",
  "head_commit": "...",
  "diff_digest": "...",
  "workflow_digest": "...",
  "prompt_digest": "...",
  "failure": {
    "kind": "capability|contract|scope|decision|environment|provider|implementation|unknown",
    "reason_code": "...",
    "needed": ["..."],
    "evidence_refs": ["event:...", "log:..."]
  }
}
```

Rules:

- `capability_declined` is an immediate routing result, not a request to retry.
- The schema requirement exists so the controller can distinguish capability, contract,
  environment, and product failures. It does not make model self-assessment authoritative.
- The wrapper verifies git/artifact fields and supplies facts it can determine mechanically.
- Missing/malformed typed output retains the raw transcript, becomes `unknown`, and can never
  authorize approval/merge.
- Markdown reports are rendered from or linked to typed evidence; regex parsing is temporary
  compatibility only and is deleted in this greenfield program.

### 4.6 Five-band capability and review model

Bands are ordinal task-demand buckets per role:

| Band | Implementation demand | Review demand |
| --- | --- | --- |
| 1 | one bounded mechanical change, explicit files/oracles, no decisions | bounded artifact with strong mechanical gate |
| 2 | localized routine logic, low ambiguity | localized semantic/test-quality review |
| 3 | normal multi-file/cross-module work | broad correctness and maintainability review |
| 4 | high-risk, novel, architectural, weak-gate, or difficult recovery | specialist/deep independent review |
| 5 | frontier architecture/recovery/final automatic escalation | frontier judgment or human fallback |

A route has role-specific eligibility and confidence. A model may implement at band 2 but
only review at band 1, or be disallowed from review entirely.

Independent review always means a distinct attempt/session with no implementer conversation
reuse. For low-risk work, policy may permit the same underlying model family through an
independent route/session; higher-risk policy may require model/provider diversity or strict
capability dominance. The warm `self_review` node remains useful but never satisfies the
independent-review prerequisite.

Review band is computed as:

```text
review_band = max(effective_implementation_band, risk_floor, gate_compensation_floor)
```

`risk_floor` considers security/permissions, architecture/public contract, data loss,
dependency/build changes, breadth/novelty, and generated or modified tests. Tests touched is
not a binary promotion rule: changing tests adds an oracle-integrity concern; behavior changed
without appropriate test changes adds a completeness concern. Both are visible findings.

Capability ladder:

1. Provider transient: bounded same-session resume/backoff; does not change task band.
2. Route capacity/unavailable: another eligible route in the same band; does not change task
   band.
3. Explicit `capability_declined`: record evidence, exclude that task/route fingerprint,
   increment effective band immediately, dispatch unchanged scope at the next band.
4. Repeated unknown/no-progress: run one stronger independent diagnosis, then apply its typed
   deterministic action.
5. Band 5 decline/failure or exhausted caps: open an actionable human decision containing the
   full ladder and evidence.

No carver turn occurs for `capability_confirmed`. `scope_too_broad`, `architectural`, or
`contract_missing` uses the carver because the work definition—not only the route—must change.

### 4.7 Cheap-agent execution packet

Keep the rich handoff as source/audit truth, but compile a bounded `ExecutionPacket` for the
selected role and band. For band 1 it includes only the objective, allowed/forbidden paths,
exact context pointers/excerpts, behavioral oracle plus negative, required environment recipe,
and result schema.

The wrapper—not the small model—owns:

- worktree/branch setup and teardown;
- git status, diff, commit, and artifact digest verification;
- gate invocation and output capture;
- usage/resource collection;
- receipt assembly and schema write.

Band-1 admission is denied when product decisions are unresolved, scope is cross-component or
ambiguous, required context exceeds the configured envelope, oracles are not mechanical, or a
new architectural choice is required. Denial is a routing decision before paying the model.

### 4.8 Route objective and learning

The selector filters hard constraints first, then optimizes expected total accepted-change
cost:

```text
expected_total_cost =
  attempt_price
  + P(retry) * expected_retry_cost
  + P(promote) * expected_promotion_cost
  + expected_review_and_gate_cost
  + human_interruption_penalty
  + latency_penalty
```

Inputs include role/band eligibility, privacy/sandbox/tool/context requirements, route health,
prepaid capacity, estimated tokens/cache reuse, task-archetype acceptance history, failure
fingerprints, confidence/freshness, and project risk. Correctness constraints are filters or
minimum confidence floors—not weights that a cheap price can overcome.

Learning records observations automatically but does not mutate policy. A policy suggestion
must include task archetype, contributing task/attempt IDs, route/model/version, sample size,
accepted/rejected outcomes, cost basis, confidence, and counterfactual estimate. The operator
accepts or rejects threshold/routing changes explicitly.

### 4.9 SQLite-only runtime and WAL

WAL is SQLite **write-ahead logging**: committed page changes are first written to a `-wal`
file and later checkpointed into the main database. It allows readers to see a consistent
snapshot while the single writer commits. It is a physical journaling mode, not nyxloom's
logical event log.

Target store rules:

- one SQLite database per project in the persistent state volume;
- event append and projection update in one transaction;
- one logical daemon writer; CLI/dashboard use bounded read connections;
- explicit schema and projection version tables;
- upcasters for retained event versions;
- SQLite backup API for online backups—never copy only the main file while WAL is live;
- deterministic JSONL export/import for audit and disaster recovery;
- bounded WAL checkpoint policy and disk-space/health metrics;
- corruption, busy/locked, disk-full, and failed-projection paths fail closed.

Because this is greenfield, remove `NYXLOOM_STATE_BACKEND`, file-state mutation paths, and
dual-backend tests after exporting any desired historical audit data. Do not maintain a
runtime compatibility layer. A destructive live cutover still requires a separately approved
operational runbook that stops the daemon, backs up/verifies the database, and names rollback.

## 5. Implementation packages and order

Package IDs below are stable execution references. DR IDs remain requirement references.

| Package | Covers | Depends on | Primary result |
| --- | --- | --- | --- |
| CR-00 | prerequisite | — | behavioral characterization and invariant corpus |
| CR-01 | DR-04 | CR-00 | authoritative document lifecycle/archive |
| CR-02 | DR-03 | CR-00 | fail-closed authoritative snapshot model |
| CR-03 | DR-13 | CR-00 | universal typed result/evidence envelope |
| CR-04 | DR-09 | CR-00, CR-03 | SQLite-only versioned event/projection store |
| CR-05 | DR-06 | CR-03, CR-04 | action/effect handler registry; thin daemon |
| CR-06 | DR-07 | CR-05 | ordered pure planning rules |
| CR-07 | DR-08 | CR-03, CR-05, CR-06 | workflow v1 compiler and lifecycle/node split |
| CR-08 | DR-05, DR-10 | CR-04, CR-07 | single route selector and durable route health |
| CR-09 | DR-01 | CR-03, CR-07, CR-08 | five-band escalation and diagnosis ladder |
| CR-10 | DR-11 | CR-08, CR-09 | cost/capability optimizer and execution packets |
| CR-11 | DR-02 | CR-05, CR-06, CR-07 | execute-boundary stale-premise admission |
| CR-12 | DR-12 | CR-04, CR-07 | criterion-level product evidence |
| CR-13 | DR-14 | CR-05, CR-08, CR-10 | sandbox/resource/permission enforcement |
| CR-14 | DR-15 | CR-04, CR-07–CR-13 | task processing trace and operator explanations |

CR-01 and CR-02 may run alongside CR-03 if their file ownership does not overlap. CR-05,
CR-06, and CR-07 are deliberately serial because they progressively reshape the same control
surface. Do not parallelize edits to `daemon.py`, `reconcile.py`, `types.py`, or workflow
schemas across those packages.

### CR-00 — behavior and invariant baseline

Work:

1. Create fixture event histories for full/gated/lean happy paths, self-review rejection,
   independent rejection classes, gate failure, merge conflict, human decision, provider
   pause, crash/replay, stale result, malformed result, and attempt exhaustion.
2. Record semantic outputs: ordered actions, emitted events, final lifecycle/node, artifact
   bindings, and waits. Avoid snapshots of incidental prose/log formatting.
3. Add property checks: every reachable runtime has progress, a durable wait, or a bounded
   terminal escalation; no merge lacks approval/gate evidence; replay is deterministic.
4. Add a source-size/ownership inventory used only for review planning, not as a product gate.

Acceptance:

- Deliberately corrupting one expected transition, artifact binding, or gate verdict makes a
  characterization test fail.
- Fixture replay produces identical semantic state twice.
- The suite runs under the real tester-unified gate without timing-dependent assertions.

### CR-01 — product truth and archive lifecycle

Work:

1. Inventory every `[refs]` document and choose one current authority per concern.
2. Move superseded/historical documents to `docs/archive/` with metadata; add
   `superseded_by` only when there is a named successor.
3. Remove non-current documents from active refs/context and update current README/spec/docs.
4. Extend lint so refs can resolve only current documents and archive paths are excluded from
   default agent packets/search manifests.
5. Generate a link map/tombstone only for paths known to require stable references.

Acceptance:

- A ref to a superseded/historical document fails lint.
- Default dispatch packets contain no archive content unless explicitly requested.
- Every declared current fact about store, merge mode, trove, daemon, and milestone agrees
  with machine configuration and the canonical direction spine.

### CR-02 — authoritative snapshot fail-closed audit

Work:

1. Define snapshot inputs as typed descriptors with `authoritative|advisory`, error policy,
   freshness, and bounded diagnostic data.
2. Replace authority-bearing broad exception fallbacks with typed unavailable/error results.
3. Generate fault-injection tests for config, state, decisions, refs, leases, routes, gates,
   git facts, receipts, and artifact digests.
4. Keep advisory telemetry degradable but visible.

Acceptance:

- Every authoritative input fault yields zero launch/merge effects and one actionable event.
- Every advisory input fault preserves allowed progress and records degradation.
- A source audit finds no unclassified broad exception in authority-bearing snapshot paths.

### CR-03 — typed result and evidence protocol

Work:

1. Define versioned discriminated result schemas for implement, self-review, independent
   review, diagnosis, carve, gates, and operator results.
2. Bind results to task/attempt/artifact/workflow/prompt identity.
3. Make wrappers populate mechanically knowable git/gate/usage fields.
4. Replace approval/reject/decline regex authority with schema validation; render markdown for
   humans from typed records.
5. Persist raw output separately as evidence and bound its digest to the result.

Acceptance:

- Stale task, wrong attempt, wrong SHA/diff, wrong workflow digest, unknown outcome, malformed
  decline, and missing evidence all fail closed.
- A valid capability decline emits exactly one typed result and is never interpreted as
  contract/environment/provider failure.
- Human markdown can change formatting without changing controller behavior.

### CR-04 — direct SQLite-only store

Work:

1. Extract pure event validation/projection from both backend modules.
2. Make SQLite the single public store implementation and remove the environment selector,
   file writes, and import cycle.
3. Introduce lifecycle/node, workflow/prompt digests, route explanations, and result evidence
   in versioned tables/events.
4. Implement backup, restore, JSONL export/import, checkpoint, schema version, and upcaster
   commands.
5. Prepare a separately approved live cutover runbook; code changes do not themselves delete
   current state.

Acceptance:

- Event plus projection rollback atomically under injected insert/project failures.
- Concurrent reader sees only prior or committed complete state under WAL.
- Backup during writes restores and replays identically.
- Disk-full, corruption, locked database, invalid event, and failed upcast produce no
  authorizing effect.
- No production reference to `NYXLOOM_STATE_BACKEND` or live file backend remains.

### CR-05 — action/effect handler extraction

Work:

1. Create handler interfaces/registry and bounded modules for attempts, reviews, carve,
   gates, merge, capability, and operator control.
2. Move effect code from `Daemon` without policy changes; inject filesystem/process/git/store
   ports explicitly.
3. Make idempotency keys and emitted-result/event types part of every handler spec.
4. Reduce `_execute` to registry lookup, input validation, invocation, and result commit.

Acceptance:

- Exactly one registered handler owns each action type; unknown/duplicate types fail startup.
- CR-00 semantic histories remain equal.
- Re-executing every effect after an injected crash is idempotent or yields a typed conflict,
  never a duplicate launch/merge/event authority.

### CR-06 — ordered pure planner rules

Work:

1. Define immutable `PlanContext` and small pure rules grouped by lifecycle concern.
2. Make priority/order explicit data; centralize conflict/resource arbitration.
3. Return actions plus structured rule-match explanations.
4. Remove workflow-specific branches from the monolithic `plan_project` as CR-07 takes over.

Acceptance:

- Rules perform no I/O, clock read, logging, environment read, or global mutation.
- Permuting input-map order cannot change planned actions.
- Two rules cannot authorize the same exclusive resource in one pass.
- CR-00 histories and progress/dead-end properties remain green.

### CR-07 — workflow compiler and runtime node model

Work:

1. Implement schema/parser, normalized IR, validation, canonical serialization, and digest.
2. Replace stage-specific global states with lifecycle plus workflow node and terminal reason.
3. Express full/gated/lean as shipped manifests and prove semantic equivalence where intended.
4. Support registered conditional branches, bounded loops, review fan-out/all-or-quorum join,
   human waits, preview/gate nodes, and capability promotion.
5. Generate state/action diagrams, UI labels, and supported outcome matrices from IR.

Acceptance:

- Each compile-time rejection condition in section 4.3 has a negative test.
- Every strongly connected component has a consumed bound.
- Removing review/gate prerequisites from a merge path fails compilation.
- Adding a second registered review node requires only manifest/handler configuration—not a
  new lifecycle enum, storage migration, renderer branch, or daemon branch.
- A task pins its execution-plan digest; config edits affect only new tasks unless an explicit
  validated migration event is approved.

### CR-08 — pure route selector and durable health

Work:

1. Implement one selector for implement, review, carve, diagnosis, and later advisory roles.
2. Separate eligibility filters from scoring; return winner, runners-up, filtered reasons,
   expected cost/confidence, and input snapshot version.
3. Persist provider/model/route enablement and pauses with reason, source, expiry, generation,
   and operator override; rebuild on restart.
4. Remove all “first healthy route” loops from call sites.

Acceptance:

- Hard privacy/tool/sandbox/role restrictions can never be outweighed by price.
- Restart during a pause preserves eligibility and expiry exactly.
- Given identical snapshots, selection and explanation are identical.
- No dispatch-capable call site selects a route outside this component.

### CR-09 — five-band capability ladder

Work:

1. Introduce requested/effective band 1–5 and route-attempt/failure fingerprint history.
2. Implement the ladder and diagnosis outcome table from section 4.6 as workflow handlers.
3. Add immediate capability-decline promotion and per-task route exclusion.
4. Map review band by implementation band plus risk/gate floor; encode role-specific model
   eligibility rather than universal strict dominance.
5. Persist every movement and cap termination with evidence.

Acceptance matrix:

- Explicit band-1 decline -> unchanged scope at band 2, never same route/task again.
- Provider failure -> same band alternate/backoff, never capability promotion.
- Contract/scope/decision/environment failure -> corresponding action, never false promotion.
- Ambiguous repeated no-progress -> one diagnosis -> typed deterministic route.
- Band-5 exhaustion -> one actionable human wait; no loop.
- Test-changing/security/weak-gate inputs raise review floor as configured; ordinary bounded
  work may use `implement-1 -> review-1` with independent eligible routes.

### CR-10 — accepted-cost optimizer and execution packets

Work:

1. Define task-archetype features and conservative outcome statistics with freshness and
   minimum sample thresholds.
2. Implement the expected-total-cost objective and confidence/risk constraints.
3. Compile role/band-specific execution packets; move deterministic receipt/git/gate work to
   wrappers.
4. Record predicted versus minimum successful band, accepted outcome cost, retries, review
   results, and policy suggestions with evidence lineage.

Acceptance:

- A nominally free route with high measured escalation cost loses to a cheaper expected-total
  route when confidence is sufficient.
- With insufficient evidence, conservative configured priors win; the system does not learn
  a threshold silently.
- A band-1 packet stays within its configured context/argv budget with realistic long paths.
- A task requiring unresolved decisions/architecture is rejected from band-1 admission before
  model dispatch.
- Wrapper-produced git/gate facts cannot be overridden by agent claims.

### CR-11 — stale-premise effect-boundary admission

Work:

1. Define a single admission token binding task, input revision/context hashes, workflow
   digest, route snapshot, budget, lease, and current main.
2. Revalidate immediately before every fresh effect; consume token once/idempotently.
3. Emit a typed drift report and route to re-scope without launching stale work.

Acceptance:

- Advance main or alter a declared context after planning but before execution: zero launch,
  typed stale-premise event, correct re-scope/wait.
- A route-health or budget change after planning invalidates admission.
- An unchanged valid task launches once.

### CR-12 — criterion-level product evidence

Work:

1. Add absent/building/proven/degraded status and evidence references per acceptance item.
2. Validate referenced tests/code/events and mark evidence stale on relevant change/removal.
3. Make gap audit operate on criterion evidence, not whole-feature prose status.

Acceptance:

- Removing/renaming a cited test or changing an invariant stales the criterion.
- One proven criterion cannot mark sibling criteria shipped.
- Evidence links resolve to current code/test/event identities.

### CR-13 — runtime sandbox and resource policy

Work:

1. Define per-task/role permission, mount, network, secret, CPU, memory, process, and wall-time
   requirements as selector constraints and handler inputs.
2. Enforce them outside the agent process in the managed runtime.
3. Record actual containment identity/resources and fail closed if requested enforcement is
   unavailable.
4. Permit free/local limited routes only inside policy-compatible sandboxes.

Acceptance:

- A prompt-injected attempt cannot read an undeclared secret/path or escape allowed network.
- Missing cgroup/sandbox enforcement prevents launch rather than degrading silently.
- Resource kill/timeout is classified separately from model capability.

### CR-14 — operator trace and explanations

Work:

1. Build a per-task processing trace from events: workflow node, action, attempt, route,
   prompt/workflow version, result, finding/insight, gate, merge, cost, and resulting action.
2. Surface route alternatives/filter reasons, failure fingerprints, band ladder position, and
   exact operator decision/action.
3. Link learned observations and policy suggestions back to contributing jobs.
4. Generate views from typed data; raw logs remain drill-down evidence, not parsed authority.

Acceptance:

- An operator can answer “which job caused this insight/promotion/policy suggestion?” without
  reading daemon logs.
- Duplicate retries/notifications collapse by stable fingerprint but remain individually
  auditable.
- Redaction removes secrets without removing evidence identity or causal links.

## 6. Cross-package verification strategy

Every package must pass its focused tests and the real `[gates.tester-unified]` gate from
`nyxloom-trove/nyxloom.toml`. Required program-level suites:

1. **Compiler negatives:** malformed definitions, missing handlers/outcomes, unsafe merge
   paths, unbounded cycles, bad joins, illegal guards, and prompt drift.
2. **Model-check/property suite:** inject every handler outcome and crash boundary into every
   reachable node; prove progress/wait/bounded-terminal closure.
3. **Artifact/evidence binding:** stale/wrong task, attempt, SHA, diff, workflow, prompt, and
   reviewer identity.
4. **Failure-routing matrix:** every typed failure kind at bands 1–5, with caps and restart at
   each boundary.
5. **Store fault suite:** rollback, lock, disk full, corruption, backup during write,
   checkpoint, restore, export/import, and upcast.
6. **Behavioral fake-agent suite:** partial/malformed output, explicit decline, no progress,
   provider failure, context overflow, gate failure, and reviewer disagreement.
7. **Security suite:** mount/network/secret/tool denial and unavailable enforcement.
8. **Cost replay:** historical fixtures under alternative policies; verify deterministic
   explanation and no silent policy mutation.
9. **Changed-line coverage and mutation gate:** use the project-declared gate; no cockpit
   green is accepted as release evidence.

Tests must assert observable behavior and negative cases. Wall-clock speed, sleep budgets,
mocked core components, and call-count-only assertions are not valid oracles.

## 7. Operational rollout

Code rollout and live-state cutover are separate approvals.

1. Keep autonomous carve/test-health/gap-audit triggers disabled throughout the redesign.
2. Land CR packages in order behind no live dual implementation except where a package needs
   temporary internal scaffolding; remove scaffolding in the same or immediately following
   package.
3. Before CR-04 live cutover, stop nyxloomd, identify the exact state volume/project DBs,
   create and verify backups/exports, initialize the new schema, replay the characterization
   corpus, and document rollback. Do not infer permission to delete old state from this plan.
4. After each core package, run one reconcile pass against a disposable copied state and
   compare typed semantic results to CR-00.
5. Pin active tasks to their workflow digest. During the redesign, drain or explicitly
   terminate old-schema tasks rather than silently migrating them mid-attempt.
6. Enable five-band routing first on synthetic/fake routes, then bounded band-1 real tasks,
   then higher-risk roles only after evidence.
7. Sandbox enforcement is a prerequisite for normal use of untrusted/free routes.

Rollback is package-specific but must restore code **and compatible state schema**. A code
rollback that cannot read newly written events is not a rollback. CR-04 therefore establishes
backup/version/upcast rules before later packages write new event shapes.

## 8. Metrics and ship criteria

Required metrics:

- expected and actual total cost per accepted change;
- first-pass acceptance and minimum successful band by task archetype;
- declines, promotions, same-band route changes, diagnoses, and human escalations;
- false-promotion audit rate by failure kind;
- review escape/rejection rate by implementation band, review band, risk, and gate rigor;
- execution-packet tokens/bytes and cold/warm cache reuse;
- planner/handler errors and fail-closed admissions;
- event/store transaction, checkpoint, backup, and replay health;
- human interruptions, time-to-answer, and duplicate notification suppression;
- causal completeness: percentage of insights/policy suggestions with resolvable job evidence.

Program ship criteria:

1. `Daemon._execute` is a registry-driven shell with no workflow-specific branch chain.
2. `plan_project` is decomposed into pure registered rules and workflow IR traversal.
3. A new registered independent-review node can be added in instance config without changing
   lifecycle enums, storage schema, daemon dispatch, or render branching.
4. All dispatch roles use one selector; all route health survives restart.
5. The five-band failure-routing matrix terminates correctly and never confuses provider,
   environment, contract, decision, and capability failures.
6. SQLite is the only live store; event/projection atomicity, backup, replay, export, and fault
   behavior are proven.
7. Every approval/decline/diagnosis is typed and bound to the exact artifact/workflow/prompt.
8. Band-1 execution packets are measurably smaller and deterministic bookkeeping is outside
   the model.
9. Operators can trace each insight, escalation, and policy suggestion to exact jobs/evidence.
10. Full tester-unified gate, changed-line coverage, mutation checks, compiler negatives,
    model-check properties, behavioral fake-agent tests, and security tests are green.

## 9. External-review checklist

Reviewers should explicitly answer:

- Does lifecycle-plus-node preserve every safety property currently supplied by
  `TASK_TRANSITIONS`, or is any invariant accidentally moved into configuration?
- Is the workflow language minimal enough to validate statically while expressing the named
  review/approval/escalation cases?
- Can any agent result or configurable guard indirectly authorize merge, bypass admission, or
  create an unbounded loop?
- Are typed decline categories sufficiently mechanical for small models, and does immediate
  promotion create an abuse/cost-amplification path?
- Is role-specific `review-N` eligibility plus risk floor stronger and clearer than universal
  model dominance?
- Does the cost objective keep correctness/capability as hard constraints rather than prices
  that can trade safety away?
- Is direct SQLite-only cutover operationally safe with the proposed backup/version rules?
- Are CR-05 through CR-07 sequenced tightly enough to avoid maintaining two control engines?
- Do acceptance proofs observe actual state/artifacts/events, including negative and crash
  cases, rather than internal calls?
- Which package still has scope too broad for a bounded independently reviewable handoff?

Any concern that changes a product choice becomes a numbered decision. A mechanical inability
to satisfy an accepted package contract becomes a typed BLOCKED result; neither is silently
resolved inside implementation.
