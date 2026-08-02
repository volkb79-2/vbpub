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
The refusal to execute arbitrary user-authored workflow code is sound. The broader D-060
rejection of a workflow language is too categorical, however. A small, versioned,
schema-validated declarative workflow definition compiled to a fixed trusted kernel would
make the system easier to change without allowing projects to bypass its invariants. The
implementation also needs a thinner code-backed rule/handler engine.

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
| Workflow flexibility | Moderate for knobs, weak for behavior | Existing stages can be composed safely; the registry does not drive runtime behavior and new kinds/outcomes remain cross-cutting changes. |
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

`P0` is the priority/severity class: address before expanding autonomous operation. It is
not a unique reference. Risks use stable `RISK-NNN` identifiers because an ordinal such as
`P0.1` becomes misleading when priority changes or findings are reordered. Delivery packages
use separate `DR-NN` IDs and cite the risk they mitigate.

### RISK-001 [P0] — Capability escalation does not close the cheap-model loop

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
  `capability`. An agent-authored `capability_declined` result uses the latter; reserve
  `capability_suspected` for a controller diagnosis of an ambiguous failed attempt.
- Keep raw agent markers as evidence, never sole authority. Combine them with deterministic
  signals: repeated same-route failures, no meaningful worktree progress, repeated oracle
  misses, or a stronger diagnosis verdict.
- Track `requested_tier` and `effective_tier` separately. Here, a **tier** is the complete
  role-plus-complexity key such as `implement-1`; its numeric suffix is the **capability
  band**. Promotion should append a typed event and dispatch the same handoff from
  `implement-1` to `implement-2`; it should not rewrite the handoff.
- Bypass a new carver turn when scope is sound. A carver is appropriate for architectural
  or stale-premise re-scoping, not for a mechanical tier bump.
- Use a bounded ladder: same-session transient resume; another eligible route within the
  same tier; promote to the next capability band; independent diagnosis; human. Every step
  needs an explicit cap and reason. Avoid bare "band" language in user-facing receipts.
- Record predicted band versus minimum band that succeeded, first-pass acceptance, total
  accepted-task cost, retries, and escalation cause. Use this local evidence to calibrate
  future carving; benchmarks should seed priors, not dictate routing.

Acceptance proof: configure `implement-1` with a fake cheap route that returns a typed
`capability_declined` result. The next attempt must execute the unchanged handoff through
an eligible `implement-2` route without asking a human, selecting the same route again, or
re-running the carver. A fake environment or missing-contract failure must not be promoted,
and the bounded ladder must terminate in an actionable human decision when no eligible tier
remains.

Add a bounded capability preflight to implementation prompts, but do not ask whether the
model merely "feels capable." Model self-confidence is weak evidence and can produce both
overconfident waste and unnecessary refusals. Ask it to proceed unless it can name a
mechanical unmet condition: missing required context, unavailable tool/access, impossible
oracle, context-window overflow, or a concrete capability mismatch. A decline must emit the
typed `capability_declined` outcome with `reason_code`, `needed`, and `evidence` before
editing, with no long reflection. Treat this as one routing signal, validate it against
observable facts and route history, and cap repeated declines.

### RISK-002 [P0] — Carved premises can be stale at first dispatch

`input_revision` is checked during rejection triage and proposal admission, but an ordinary
queued handoff is not revalidated against current `main` immediately before a fresh
implementer dispatch. This is already recorded as backlog B12. Restoring
`carve_ahead_target = 10` before fixing it increases the chance that a cheap model faithfully
implements obsolete assumptions.

Recommendation: make premise validity part of the single execute-time admission token.
Before every fresh attempt, prove that the handoff revision is still an ancestor of current
main and that declared context hashes, if present, still match. Route stale work to the
carver with a typed drift report. Do not restore carve-ahead until this is green.

### RISK-003 [P0] — Declared product references are not authoritative in practice

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

Recommendation: establish one authority per concern. A `superseded` document was once
authoritative and has a named successor; a `historical` document is retained as a snapshot,
experiment, or provenance record and need not have a one-to-one successor. Both should be
removed from `[refs]` and normal agent context. Physically move them to `docs/archive/`
(preserving Git history), add `superseded_by` when applicable, and leave a tombstone only
where stable external links require one. Reserve `legacy-workflow-origin/` for pre-nyxloom
provenance rather than mixing it with product documentation. Add schema metadata such as
`lifecycle = current|superseded|historical` and lint that `[refs]` may name only current
documents. Add contradiction checks for a small set of machine-known facts: state backend,
trove path, merge mode, active milestones, and daemon mode.

### RISK-004 [P0] — Fail-open exception handling remains a systemic risk

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

The current default menu contains seven registered stage kinds. The configured tuple is
`carve, implement, self_review, review_independent, triage, auto_merge,
post_merge_gate`, but this is not a simple seven-step conveyor: task state determines which
branch runs, and `triage` is the rejection branch.

| Stage | Current responsibility | Important limitation |
| --- | --- | --- |
| `carve` | Convert direction/backlog or a re-scope request into validated handoffs. | Cannot mechanically split an already-running task after a capability failure without another carver turn. |
| `implement` | Dispatch an implementation agent into an isolated worktree and collect its attempt result. | Failure and capability outcomes are not yet a dependable typed escalation ladder. |
| `self_review` | Reuse the implementer's warm session to check its own diff before independent review. | Cheap, but shares the implementer's blind spots and is not an approval authority. |
| `review_independent` | Have an independent agent approve or reject the actual git artifact. | One generic review stage; no risk-selected specialist chain or quorum. |
| `triage` | Route a rejection class to retry, re-scope, decision, or another wait state. | Much of the classification originates in reviewer prose and hard-coded planner branches. |
| `auto_merge` | Deterministically perform the guarded merge after approval. | Merge policy is code/config, not a reusable workflow action with declarative preconditions. |
| `post_merge_gate` | Run the real project gate on the merged state and complete or block. | No declarative compensation/rollback or deploy-preview branch. |

The effective happy path is:

```text
carve -> implement -> self_review -> review_independent
                                      | approve -> auto_merge -> post_merge_gate -> complete
                                      ` reject  -> triage -> retry | re-carve | decision
```

Likely useful flows the menu cannot express cleanly today include a risk-selected test or
security review, two independent reviewers with an `all`/quorum join, an explicit human
approval wait, preview-environment validation, conditional gates based on touched artifacts,
bounded capability promotion without re-carving, and a compensation/rollback action after a
post-merge failure. These are sufficient concrete cases for workflow v1; nyxloom does not
need a general BPMN engine.

DSL means **domain-specific language**: a deliberately small notation for describing one
domain. For nyxloom it could be versioned TOML/YAML that declares stages, transitions,
guards, retry/escalation policy, prompt-template IDs, and evidence requirements. It need not
be a general programming language and it must not permit arbitrary Python, shell, imports,
or unregistered state mutation.

D-060 correctly rejects arbitrary user-defined executable behavior, but it conflates that
with every declarative workflow definition. The current boundary is too restrictive for the
product goal. A safe middle path is a **declarative workflow manifest compiled to a fixed,
typed kernel of registered handlers**:

```yaml
schema: nyxloom.workflow/v1
start: implement
nodes:
  implement:
    handler: dispatch_agent       # registered handler, never arbitrary code
    prompt: implement/v2
    tier: implement-1
    outcomes:
      completed: self_review
      capability_declined: escalate_implement
      contract_missing: human_decision
  escalate_implement:
    handler: promote_capability
    config: {max_band: 5}
    outcomes: {promoted: implement, exhausted: human_decision}
```

The compiler would resolve names, type-check outcomes and guards, prove graph closure and a
bounded path to wait/terminal states, apply mandatory admission/review/gate constraints,
and emit an immutable execution plan with a digest. Workflow node IDs should no longer be
encoded as members of the global `TaskState` enum. Store a stable coarse lifecycle
(`open|running|waiting|terminal`) plus `workflow_node_id`, node status, and terminal reason;
validate node movement against the compiled plan. This lets an instance add a review or
approval node without a new database/state enum while keeping lifecycle and mutation rules
inside the trusted kernel. Only nyxloom code could add a new effectful handler or weaken an
invariant.

This does not make complexity disappear. It adds a schema, compiler, version migration,
diagnostics, and replay compatibility. Its value is concentrating complexity once instead
of repeating it in planner branches, executor branches, renderers, prompts, and docs. The
manifest should therefore start internal and version-controlled; expose project/user edits
only after static validation, shadow replay, and clear error messages are proven.

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

Recommended target:

1. Replace workflow-specific `TaskState` members with a small hard-coded task lifecycle plus
   `workflow_node_id`; keep lifecycle legality, terminal reasons, event append, storage
   projection, and admission hard-coded in the trusted kernel. Compile legal node transitions
   from the workflow definition.
2. Define code-backed `StageHandler`/`ActionHandler` records containing stage metadata,
   a pure planning function, an effect handler, evidence schema, and retry policy.
3. Define `nyxloom.workflow/v1`, compile it at load into an immutable execution plan, and
   validate ownership, transition legality, graph closure, termination bounds, prompt IDs,
   and mandatory safety stages.
4. Dispatch actions through a handler map instead of the giant `isinstance` chain.
5. Split effectors by bounded concern: attempts, reviews, carver, gates, merge, and operator
   control. Keep `Daemon` as orchestration only.
6. Generate the state legend and supported-action matrix from the same registry so UI and
   runtime cannot drift.
7. Persist `workflow_digest`, `prompt_digest`, matched guard, selected transition, and
   handler version on every attempt so old runs remain explainable after config changes.
8. Allow project overlays only on explicitly declared policy fields; require a code change
   and invariant tests for new handlers, state mutation kinds, or safety exemptions.

This preserves the fixed invariant kernel while making workflow changes local, inspectable,
replayable, and eventually project-tunable. It should replace—not sit beside—the current
pipeline registry once parity is proven.

### Cheap agents need a compiled execution packet, not the full control contract

The detailed handoff doctrine is valuable for the carver, reviewer, and audit trail, but it
can overburden a small implementer if the model must interpret the complete doctrine, manage
git bookkeeping, run infrastructure, format receipts, diagnose its own capability, and write
the patch in one turn. Do not weaken the source handoff. Compile a smaller, role/band-specific
`ExecutionPacket` from it.

For a band-1 implementation, the packet should normally contain only:

- one concrete objective and the observable that defines success;
- exact allowed files and forbidden boundaries;
- exact context pointers or bounded excerpts, not the whole product history;
- explicit mechanical oracles and negative cases;
- registered tools/environment recipe required for this task;
- the typed result/decline schema.

Move deterministic work out of the model: the wrapper should establish the worktree, inspect
git truth, record changed files/commit identity, run declared gates, capture resource usage,
and construct the receipt. The agent should produce the patch plus concise structured facts.
Band-1 admission should reject work with unresolved product decisions, architectural choice,
broad cross-component effects, unclear oracles, or an execution packet above its measured
context/complexity envelope. That is how detailed contracts enable cheap models rather than
exclude them.

The operator currently wants this configuration at the nyxloom-instance level, not editable
by managed projects or end users. Keep workflow source, prompt templates, risk policy, and
band definitions in instance-owned configuration initially. Design schemas for later
project overlays, but do not expose or support them in the first implementation.

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

Replace the universal “strictly stronger reviewer” rule with a risk/capability rule. Numeric
review bands are role-specific: `review-1` means the lowest review tier that is still
eligible to review bounded low-risk work, not “the same weak model used by `implement-1`.”
Normally map `implement-N` to at least `review-N`, then raise the review floor for security,
architecture, broad scope, weak gates, generated/mutated tests, or low capability confidence.
A test change is not automatically safer: changing an oracle can make a bad implementation
look green, so test modifications require explicit independent test-quality review. Strict
model dominance remains a policy for high-risk work, not a universal invariant. Independent
means a fresh attempt/session without implementer conversation reuse; lower-risk policy may
still use the same model family, while high-risk policy may require model/provider diversity.

Use five configurable capability bands rather than three. The names describe task demand and
role eligibility, never a permanent provider mapping:

| Band | Intended demand | Typical route class |
| --- | --- | --- |
| 1 | Tiny, bounded, mechanically specified work | local/free small models |
| 2 | Localized routine work with clear tests | economy models |
| 3 | Normal multi-file implementation/review | mainstream workhorse models |
| 4 | Cross-component, ambiguous, or high-risk work | strong models/high reasoning |
| 5 | Frontier architecture, recovery, and final escalation | frontier models/maximum reasoning |

Each role has its own eligibility thresholds, so a model may qualify for `implement-2` but
not `review-2`. An explicit typed capability decline immediately promotes the unchanged task
to the next band and excludes the declining route for that task fingerprint; it is never
given back to the same model as another ordinary retry. The schema makes the decline
machine-readable—it does not justify repeating the attempt.

A stronger independent diagnosis is reserved for ambiguous evidence, repeated no-progress
failures, conflicting classifications, high-cost promotion, or band-5 failure. Its outcomes
and deterministic actions should be:

| Diagnosis | Controller action |
| --- | --- |
| `capability_confirmed` | Promote unchanged scope one band. |
| `implementation_defect` | Retry with concrete evidence, normally another route in the same band. |
| `contract_missing` | Repair/re-carve the contract; do not promote. |
| `scope_too_broad` or `architectural` | Split/re-scope through the carver. |
| `environment` or `provider` | Repair/pause infrastructure or change route; do not promote task demand. |
| `decision_required` | Open a typed operator decision. |
| `unclassified` or band-5 failure | Escalate to a human with the complete evidence chain. |

### Local small-model sidecar

A quantized CPU model is plausible as an advisory sidecar and could reduce paid diagnosis
and summarization calls. It is not zero-cost: it consumes RAM, CPU time, electricity, startup
latency, model storage, and operational attention. Call it **zero marginal API cost**.

Good bounded uses:

- extract typed fields from noisy agent/CLI output after deterministic adapters fail;
- summarize prior attempts into an evidence-linked context packet;
- classify an unknown failure into candidate `FailureKind` values;
- cluster recurring failure fingerprints and propose a known remedy;
- rank already-eligible routes or flag that the deterministic selector has low confidence.

Do not let it authorize transitions, waive gates, merge, rewrite scope, or invent evidence.
The deterministic daemon must validate its JSON schema and every cited event/artifact, fall
back cleanly when it is unavailable, and record model/version, prompt digest, input hashes,
output, latency, and confidence. Prefer regex/typed adapter extraction first and invoke the
model only for the unclassified tail. Evaluate it offline on historical events with a
confusion matrix, especially false capability classifications/promotions and lost blocker facts,
before enabling live advice.

Run it out of process behind a narrow local API so daemon correctness and availability do
not depend on model runtime health. A 1B–8B quantized model is a reasonable experiment range;
choose the smallest model that meets a held-out extraction/classification suite on the
actual host. Summaries must retain links to raw evidence and should never replace it.

Given the operator's 6 GB resident hot-set target, start with
[Qwen2.5-Coder-3B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-3B-Instruct)
at a measured 4-bit quantization. It is instruction-tuned, code-specific, about 3.09B
parameters, and has a 32K advertised context; cap live context far below the maximum until
RSS, KV-cache growth, and tokens/second are measured. Use
[Qwen2.5-Coder-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-Coder-1.5B-Instruct)
as the lower-memory baseline. Compare
[Granite 4.1 3B](https://huggingface.co/ibm-granite/granite-4.1-3b) for typed
classification/tool-following, although it is general-purpose rather than code-specific.
Qwen2.5-Coder-7B may fit its quantized weights near the budget but leaves too little reliable
headroom for KV cache and runtime overhead, so treat it as cold/on-demand only unless
measurement proves a stable hot set.

Do not treat 40 GB of swap as usable active-model capacity. Inference repeatedly scans model
weights; swapped active pages can turn every token into page-fault churn. Quantized weights
may also compress poorly in zswap. Monitor process RSS/PSS, cgroup memory, memory PSI,
major-fault rate, swap-in/out throughput, tokens/second, and p95 request latency. Apply a
circuit breaker that unloads/restarts the sidecar or rejects advisory work when sustained
memory pressure or throughput degradation crosses measured thresholds. Cold libraries and
inactive pages may migrate to swap; the active weights and KV cache must stay resident.

## Storage and event sourcing

The event-first design is appropriate. The file backend has a known append-then-projection
crash window healed by replay; SQLite can make event and projection atomic. Maintaining both
indefinitely doubles semantic tests and complicates every storage change.

Recommendation:

- Keep markdown/trove files as human product truth.
- Because nyxloom is greenfield and has no external storage compatibility obligation, make
  SQLite the single runtime event/projection store now; remove the backend selector and dual
  live implementation rather than funding a prolonged shadow migration.
- Retain an append-only JSONL export command for greppability and disaster recovery rather
  than a second live backend.
- Extract pure event validation/projection from `storage.py` so the backend does not import
  back through its facade.
- Add event schema upcasters and explicit projection versioning before v1 format stability.

WAL means **write-ahead logging**, SQLite's journal mode already enabled by
`storage_sqlite._connect`. A transaction writes new pages to a separate `-wal` file before
checkpointing them into the main database, allowing readers to observe a consistent prior
snapshot while one writer commits. WAL improves atomicity/concurrency; it does not replace
nyxloom's logical append-only event table, event validation, backups, or replay tests.

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
- The self-host trove is not currently lint-green: six warnings and one blocking L7 error.
  The error is in the old P42 handoff, whose `scope.forbid` references nonexistent
  `nyxloom-trove/STANDARD.md`. Completed artifacts remaining in the live handoff glob make
  historical drift an active ship-gate failure; archive or migrate them deliberately.

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

## External systems worth comparing

These systems occupy different layers; none is an obvious drop-in replacement for nyxloom's
evidence-bound, multi-project reconciliation core.

| System | What it is | Relationship to nyxloom | What to borrow or test |
| --- | --- | --- | --- |
| [Factory Factory](https://github.com/purplefish-ai/factory-factory) | Local UI/workspace manager for parallel Claude/Codex worktrees, issue-to-PR flow, and a PR repair “ratchet.” | Closest contender for operator UX and worktree/PR progression; more complementary than equivalent to nyxloom's trove, gates, event replay, and capability policy. It is unrelated to Factory.ai. | Evaluate its workspace UI, ACP integration, resumable sessions, and repair-station UX; do not assume git worktrees are a security sandbox. |
| [Ferrox Factory](https://cgcone.com/plugins) (`ferrox-core`) | Public registry describes it as a meta-prompting, context-engineering, spec-driven skill/plugin system. | Appears to address authoring/context quality above an agent, not durable orchestration below it. Public evidence is too thin for a deeper architecture claim. | Inspect its actual source/license and compare spec lifecycle, prompt modularity, receipts, and context selection before considering reuse. |
| [GitHub Agentic Workflows](https://github.com/github/gh-aw) | Natural-language Markdown workflows compiled to GitHub Actions with schema checks, pinned dependencies, sandbox/network controls, safe outputs, and cost controls. | Strongest reference for “editable definition compiled to a guarded runtime”; a contender for GitHub-scoped scheduled/event automation, not the whole local nyxloom control plane. | Borrow source-to-lockfile compilation, static diagnostics, safe-output separation, workflow digests, and audit/cost tooling. |
| [LangGraph](https://github.com/langchain-ai/langgraph) | General durable, stateful agent-workflow framework with human-in-the-loop support. | A possible orchestration substrate, but adopting it would duplicate or transfer ownership of nyxloom's strategic event/state semantics. | Prototype only if maintaining durable waits/replay becomes commodity burden; compare crash recovery and event explainability first. |
| [OpenHands Software Agent SDK](https://github.com/OpenHands/software-agent-sdk) | Typed agent/actions/tools/workspaces with local or isolated remote execution. | A contender for agent runtime/adapters and sandbox plumbing, not necessarily for product doctrine, carving, review, or merge policy. | Compare typed action/observation contracts, provider abstraction, context condensation, workspace isolation, and stuck detection. |
| [Factory.ai](https://factory.ai/product/software-factory) | Commercial model-independent full-SDLC platform with routing, governance, quality gates, and outcome analytics. | Strategic product benchmark, distinct from Factory Factory; replacement evaluation depends on sovereignty, audit access, price, and vendor control. | Borrow outcome metrics such as cost per accepted/merged change and autonomy ratio; require evidence for any performance claim. |

Retain this as a pattern catalogue, not an adoption workstream. If a future build-versus-buy
decision arises, compare representative handoffs on accepted outcome cost, human turns,
crash recovery, failure attribution, route escalation, gate independence, workflow edit
effort, evidence retention, sandbox strength, and ownership of durable state.

## Recommended delivery order

Autonomous carving is not the milestone. In a greenfield system, first create the seams that
make policy safe and cheap to change; then implement escalation on those seams instead of
adding another hard-coded path to the monolith.

### Phase A — truth and typed boundaries

1. **DR-04 Product truth cleanup (RISK-003).** Archive stale `[refs]`; enforce
   current/superseded/historical lifecycle.
2. **DR-03 Fail-open audit (RISK-004).** Classify authoritative versus advisory inputs and
   fault-test every authority-bearing snapshot.
3. **DR-13 Typed agent result.** Generalize the typed artifact beyond review: completion,
   early capability decline, block, diagnosis, and review must all bind to task, attempt,
   head SHA/diff digest, workflow digest, and evidence.
4. **DR-09 SQLite-only runtime store.** Remove the live file backend and backend selector;
   retain JSONL export, versioned events, replay, and backup/restore tests.

### Phase B — redesign the core before adding behavior

5. **DR-06 Action-handler extraction.** Split attempts, reviews, carver, gate, merge, and
   operator effectors out of `Daemon._execute`, preserving observed behavior.
6. **DR-07 Planner rule extraction.** Split `plan_project` into ordered pure rules over a
   shared immutable plan context.
7. **DR-08 Workflow compiler v1.** Compile instance-owned manifests into registered planning
   and effect handlers; support typed outcome edges, safe guards, bounded loops, human waits,
   and review fan-out/quorum while keeping state mutation in the kernel.
8. **DR-05 Route selection seam.** Route every role through one pure selector with complete
   filtered/winner explanations.
9. **DR-10 Durable provider/route health.** Rehydrate disable/pause state from events with
   expiry, reason, and operator override.

### Phase C — implement the cost/capability behavior on the new core

10. **DR-01 Five-band capability ladder (RISK-001).** Add requested/effective band,
    immediate typed-decline promotion, task-route exclusion, caps, diagnosis only for
    ambiguity/top-band failure, and behavioral matrix tests.
11. **DR-02 Stale-premise admission (RISK-002/B12).** Revalidate at every fresh dispatch
    effect boundary. This remains required even though autonomous carve-ahead is not a goal.
12. **DR-11 Cost/capability selector.** Minimize expected total cost per accepted change
    subject to risk/capability constraints, using task-archetype outcome history and five
    role-specific bands. Add the compiled cheap-agent `ExecutionPacket`.

### Phase D — complete evidence, containment, and human control

13. **DR-12 Criterion-level reality evidence.** Give product-definition acceptance items
    evidence/status and drive gap audits from them.
14. **DR-14 Runtime sandbox.** Finish F010 before treating untrusted/free implementers as a
    normal route; prompt text is not a security boundary.
15. **DR-15 Human trace and explanations.** Surface which job produced each insight, selected
    route and alternatives, failure fingerprint, band movement, evidence, workflow revision,
    and the exact action/decision required from the operator.

DR-16 (local advisory model) and DR-17 (contender spike) remain research notes only and are
excluded from the implementation program. The externally reviewable package/dependency and
acceptance plan is in
[`CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02.md`](CORE-REDESIGN-IMPLEMENTATION-PLAN-2026-08-02.md).

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

Verification evidence:

- Full `tester-unified` pytest phase: passed, with the one pre-existing strict xfail for the
  orphan `DRAFT` state.
- Changed-line coverage for commit `d18aa9d3` versus its direct parent: 8/8 changed
  executable lines, 100% against the 100% floor.
- The configured `--base main` coverage invocation correctly returned NO MEASUREMENT after
  the direct-main commit because `main == HEAD`; the same suite coverage was therefore
  evaluated against `HEAD^`, the direct commit's actual ancestor.
- Project lint: 7 existing findings (6 warnings, 1 error); no finding is caused by this
  report or the code changes.

## Operator decisions resolved on 2026-08-02

1. **Capability escalation:** an explicit capability decline promotes immediately to the
   next band; the same model does not receive the same task again. Independent diagnosis is
   for ambiguous/repeated failures or terminal escalation, with the typed outcome/action
   table defined above.
2. **Band count:** plan for five role-specific bands spanning self-hosted/free small models
   through frontier maximum-reasoning routes. Band definitions remain configurable and are
   calibrated by observed task-archetype outcomes.
3. **Review policy:** risk-dependent. `implement-1 -> review-1` is normal provided the
   `review-1` route is independently capable of the review job. Test modifications affect
   risk because they can weaken or falsify the oracle; route review by touched artifact,
   gate rigor, scope, and risk, not by a universal `review = implementation + 1` formula.
4. **Optimization objective:** minimize expected total cost per accepted correct change,
   including failed attempts and escalation, rather than minimizing the first call's price.
   Learn model success/failure by task archetype and expose which jobs produced each insight.
5. **Workflow configuration:** build instance-owned configuration now. Project/user-editable
   workflows and prompt criteria may be designed for but are not part of the first release.
6. **Workflow v1 assumptions:** support the current flow plus registered conditional review,
   review fan-out/quorum, human waits, preview/gate actions, and bounded escalation/repair
   loops. Do not build arbitrary scripting or a general BPMN engine.
7. **Prompt ownership:** project-owned prompt templates may be useful later, not now. Persist
   prompt IDs/digests from the start so future controlled overrides remain explainable.
8. **Storage:** make SQLite the only live runtime store now. No prolonged compatibility or
   shadow-backend period is required; JSONL remains export/recovery, not a live backend.
9. **Routing learning:** update outcome observations/confidence automatically, while band
   threshold and policy changes remain operator-approved. Every insight must link to the
   task, attempt, route, workflow/prompt version, evidence, and resulting policy suggestion.
10. **Document lifecycle:** move both superseded and historical documents out of active
    paths/context as recommended, retaining successor links/tombstones only where useful.
11. **Local-model constraints:** CPU is available; target a resident hot set under 6 GB.
    Swap/zswap may hold cold pages but must not hide active-model thrashing. Instrument and
    circuit-break on memory pressure, faults, swap traffic, latency, and throughput. DR-16
    remains outside the implementation program.
12. **Contenders:** retain research notes and borrow useful patterns only. Do not plan a
    platform migration or DR-17 implementation.
13. **Program order:** redesign the greenfield core first. Autonomous-carving unfreeze is not
    a target; DR-06 through DR-10 and their typed/storage prerequisites precede the new
    escalation behavior.

No product decision remains blocking for the companion core-redesign plan. External reviewers
should challenge the DSL safety boundary, five-band semantics, review-risk formula, direct
SQLite cutover, package dependency graph, and whether each acceptance proof observes behavior
rather than implementation structure.
