# handoffctl specification

Status: **draft design for pilot validation**. The normative terms in this
document define intended behavior; they do not claim that an implementation
currently exists.

## 1. Terms

The words MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY describe requirements for
a future conforming implementation.

- **Project**: a registered repository and its adapter configuration.
- **Handoff**: a versioned, bounded task contract.
- **Task**: the durable workflow object created from a handoff.
- **Attempt**: one concrete role/provider execution for a task.
- **Gate**: a project-declared, deterministic validation action.
- **Lease**: exclusive or counted ownership of a named resource.
- **Evidence**: provenance for a claim about an exact artifact.
- **Milestone**: the user-approved objective that bounds carving and stopping.

Draft record formats are defined in [`../schemas/`](../schemas/).

## 2. Sources of truth

1. Product behavior MUST come from project specifications and recorded user
   decisions.
2. Workflow policy MUST come from the versioned project record and this
   specification.
3. A handoff MUST NOT override a project hard policy.
4. Runtime route selection MUST be recorded on the attempt; it MUST NOT be
   inferred later from a mutable routing document.
5. Runtime state MUST NOT be reconstructed solely from chat history or an AI
   session. The event store is authoritative.
6. A projection MAY be rebuilt from append-only events without invoking AI.

## 3. Identifier and revision requirements

- Projects MUST have stable IDs.
- Tasks, attempts, events, gates, runs, decisions, and leases MUST have IDs
  unique within their documented namespace.
- Every handoff MUST record an immutable `input_revision` identifying the
  source/spec/roadmap snapshot used to carve it.
- Every attempt MUST bind to an exact base commit before it starts.
- Review, merge, and validation MUST record exact commits separately.
- If relevant product inputs changed after carving, the task MUST return to a
  stale/re-carve decision before dispatch or merge.

## 4. Task state machine

The following states are normative for the pilot domain model:

| State | Meaning |
| --- | --- |
| `DRAFT` | Candidate lacks an approved dispatchable contract. |
| `NEEDS_DECISION` | A user/product decision blocks useful progress. |
| `READY_TO_CARVE` | Product scope exists and a frontier role may write a contract. |
| `CARVED` | A validated handoff exists. |
| `QUEUED` | Dependencies/resource class permit scheduling when capacity exists. |
| `ACTIVE` | At least one current attempt is executing or being resumed. |
| `AWAITING_REVIEW` | Implementation receipt and evidence are ready. |
| `REVIEW_REJECTED` | Independent review rejected the artifact; disposition required. |
| `MERGE_READY` | Review accepted the exact commit and required pre-merge gates passed. |
| `MERGED` | A merge commit exists, but post-merge validation is not complete. |
| `VALIDATING` | Required validation is running against the recorded merge commit. |
| `COMPLETED` | Contract and all required gates are satisfied with evidence. |
| `BLOCKED` | A mechanical, provider, environment, or external blocker prevents progress. |
| `SUPERSEDED` | A newer task/decision makes this task obsolete. |
| `CANCELLED` | Authorized operator ended the task without completion. |

Transitions MUST be validated by code. Unknown or impossible transitions MUST
be rejected and recorded as an audit event. `COMPLETED`, `SUPERSEDED`, and
`CANCELLED` are terminal. A blocked task MUST carry a typed blocker and an
explicit unblock condition.

## 5. Attempt state machine

| State | Meaning |
| --- | --- |
| `CREATED` | Route and role are recorded; no process has started. |
| `PREFLIGHTING` | Capability, credentials, gate reachability, and worktree are checked. |
| `RUNNING` | The adapter owns a live process/session. |
| `STALLED` | Deterministic liveness policy found insufficient progress. |
| `INTERRUPTED` | Process stopped before a normal receipt; resume may be possible. |
| `EXITED` | Process exited and supplied a parseable completion receipt. |
| `FAILED` | Process or adapter failed and no acceptable receipt exists. |
| `ABANDONED` | Authorized policy ended recovery for this attempt. |

An adapter MUST capture the resume handle as soon as it is available. A retry
MUST preserve prior attempt events. A provider limit MUST pause that provider,
not silently consume retry budget as an implementation failure.

Liveness checks MUST use multiple signals where possible: process state, log
movement, child/gate activity, elapsed thresholds, and adapter events. Log
silence alone MUST NOT cause a kill while a declared long-running gate is
making observable progress.

## 6. Gates

1. Gates MUST be declared by trusted project configuration or a trusted project
   adapter. Model output MUST NOT create arbitrary executable/argv fields.
2. Every gate MUST declare phase, timeout, environment, and success condition.
3. Results MUST contain start/end UTC timestamps, exit status, exact commit,
   environment fingerprint, bounded redacted output, and artifact references.
4. A timed-out or hung gate is a failure, never a pass.
5. Implementation-environment results are evidence, not automatically the merge
   verdict. Project policy defines the canonical gate environment.
6. Post-merge validation MUST run against the exact merge commit.
7. Flake retries, if allowed, MUST be bounded and all attempts MUST remain
   visible. A later green MUST NOT erase earlier red evidence.

## 7. Review and merge invariants

- Merge-gating review MUST be independent of the implementation session. The
  risk policy SHOULD prefer a different provider/model family where practical.
- A self-review MAY provide cheap mechanical triage but MUST NOT gate merge.
- The review packet MUST bind handoff revision, base commit, candidate commit,
  diff digest, implementation receipt, prior gates, and explicit review scope.
- Review findings MUST distinguish contract defects, implementation defects,
  test/oracle defects, environment defects, and product decisions.
- Review fixes MUST produce a new exact candidate commit requiring the declared
  revalidation.
- Merge operations MUST be serialized per project.
- An automated merge path MUST remain disabled until the pilot demonstrates
  exact-commit review and validation provenance.
- High-risk project policy MAY require explicit human approval even after
  frontier review.

These are process guarantees, not a claim of semantic correctness.

## 8. Resources and leases

- Resources MUST use atomic leases managed by the control plane.
- A lease MUST record resource, owner, acquisition, expiry, renewal identity,
  and release event.
- Exclusive resources MUST have at most one live owner.
- Counted resources MUST enforce capacity.
- Lease recovery MUST confirm process/session identity rather than relying only
  on wall-clock age.
- Host-global resource aliases MUST allow two projects to refer to the same
  physical resource.
- Merge, live-stack, publisher, browser, GPU, and test-runner resources MAY use
  the same lease mechanism.

## 9. Scheduling and budgets

The scheduler MUST NOT call a model to poll, rank already-declared numeric
priority, check process state, or render status.

Dispatch eligibility requires:

- task state `QUEUED`;
- all hard dependencies `COMPLETED`;
- input revision still current;
- required leases available;
- provider and project WIP capacity;
- route capability match; and
- remaining time/cost/retry budget.

Scheduling SHOULD provide fairness across projects and MUST prevent a project
from exceeding configured host/resource caps. Every attempt MUST retain actual
usage when supplied and separately labeled estimates when actual cost is not
available. Currencies MUST NOT be silently combined without an explicit rate
and timestamp.

## 10. Carving and stop outcomes

Queue depth is a target only while an active milestone admits useful work. A
daemon MUST NOT keep dispatching carvers solely to maintain a numeric floor.

A carver response MUST select exactly one outcome:

| Outcome | Required effect |
| --- | --- |
| `CANDIDATES_READY` | Validate proposed handoffs; queue only accepted candidates. |
| `MILESTONE_COMPLETE` | Stop carving and run/verify milestone completion gates. |
| `ROADMAP_EXHAUSTED` | Stop and notify that no approved candidate remains. |
| `SPEC_GAP` | Stop affected lineage; create a planning/specification need. |
| `DECISION_REQUIRED` | Stop affected lineage and open a typed user decision. |
| `EXTERNAL_BLOCKER` | Stop affected lineage until its external condition changes. |
| `BUDGET_EXHAUSTED` | Stop new dispatch; preserve resumable state and notify. |

A spec/roadmap sufficiency audit MUST flag at least:

- no observable acceptance criterion;
- contradictory authority or status;
- missing owner, milestone, dependency, or decision;
- public behavior/trust/persistence/lifecycle/cost change without a decision;
- missing canonical test environment or oracle;
- repeated review rejection caused by contract ambiguity;
- stale source revision;
- task scope that cannot be bounded; and
- repeated review-derived descendants without milestone progress.

## 11. Adapter contracts

### Project adapter

A project adapter MUST expose repository identity, default branch, instruction
entry points, product-truth locations, handoff discovery, canonical gates,
worktree policy, resources, redaction, and retention. It MUST NOT teach the
generic scheduler product-specific behavior beyond declared interfaces.

### Agent adapter

An agent adapter MUST support capability preflight, launch, status/liveness,
interrupt, resume when available, normalized receipt, usage/cost extraction,
and redaction. Unsupported capabilities MUST fail closed before dispatch.

### Git adapter

The Git adapter MUST protect user changes, avoid destructive reset semantics,
record exact refs, refuse ambiguous dirty state, and manage worktree retirement
under project retention policy.

### Gate adapter

The gate adapter MUST execute only trusted declarations, enforce timeout and
resource lease requirements, and emit normalized evidence.

### Notification adapter

A notification adapter consumes normalized workflow events. Delivery failure
MUST NOT mutate workflow truth, though persistent failures SHOULD be visible.

## 12. Events and persistence

- Events MUST be append-only and have project, sequence, UTC timestamp, actor,
  and event type.
- Replaying events MUST deterministically rebuild current projections.
- A transaction MUST atomically append an event and update its projection or
  use a recoverable projection-rebuild scheme.
- Event payloads MUST be versioned and bounded.
- Large logs/transcripts MUST remain referenced artifacts rather than database
  payloads.
- Raw secrets and credential material MUST NOT enter event payloads.
- Importers MUST preserve source provenance and mark inferred data explicitly.

SQLite WAL is the proposed pilot store. Multi-host distributed scheduling is
out of scope for the initial pilot; project repositories may be on the same
host while still using independent project adapters.

## 13. Dashboard

The pilot dashboard MUST be read-only and MUST NOT invoke an AI service. It MUST
show current and historical tasks, attempts, gates, leases, decisions,
dependencies, commits, usage/cost, and sanitized live output. State changes
remain audited CLI operations.

The server MUST bind to loopback by default. Remote exposure requires explicit
configuration, authentication, and TLS termination. Artifact access MUST be
restricted to registered state roots and verified references.

## 14. Security requirements

- Treat source, prompts, model output, logs, reports, and external output as
  untrusted.
- Use allowlisted executables and structured argv.
- Never execute a command proposed in a completion receipt.
- Enforce project/worktree boundaries and least privilege.
- Redact before persistence into dashboard-visible stores.
- Bound input, logs, output, event payload, and artifact sizes.
- Reject path traversal, symlink escape, and artifact digest mismatch.
- Record authorization for cancel, supersede, approve, lease override, and
  merge operations.
- Keep raw transcripts more restricted than sanitized projections.
- Do not expose provider/session credentials or resumable secrets in the UI.

## 15. Pilot acceptance

The design may advance from shadow to pilot only when:

1. Schemas and transitions have unit/property tests.
2. Existing handoffs can be imported read-only with a drift report.
3. Event replay reproduces projections.
4. Agent adapters are tested against captured fixtures before live dispatch.
5. Worktree and lease crash recovery is tested.
6. The dashboard reads only local normalized state and uses no model call.
7. One low-risk project wave runs in shadow against the existing manual process.
8. The shadow comparison has no unexplained dispatch, dependency, or evidence
   disagreement.

Automatic merging is a later, separately approved milestone.
