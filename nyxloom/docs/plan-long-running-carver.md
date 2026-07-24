# Plan — long-running, resumed, externally-compacted strategic carver

## Executive summary

Replace the current fresh-per-`CarveDispatch` carver with one logical, project-scoped strategic carver session whose provider session id, generation, route snapshot, event cursor, and health are durable in nyxloom's event-sourced state. The daemon will bootstrap that session from the configured four-file direction spine, open decisions, and current machine work-state; thereafter it will resume the same session for merge-digest feeds, re-scopes, queue-refill carves, test-health carves, and human intake. Every merge produces a bounded typed digest rather than causing a repository re-scan, and every carver result is a schema-checked proposal that the pure `plan_project()` state machine admits mechanically. A separate `review_independent` stage remains the implementation merge gate. Context compaction is daemon-triggered at measured size/turn thresholds, preserves spine/open decisions/recent merges, and is deliberately abstracted behind the sibling compaction-harness research result. The recommended route is `carve-3` on a highest-judgment reasoning/agentic model (initially GPT-5.6 sol at high effort), while review is capability-matched to the implementation rather than automatically consuming the most expensive model. Rollout is six gated packages and is disabled for carve-less `gated`/`lean` pipelines.

## 1. Outcome and boundaries

### 1.1 Required outcome

For each project whose composed pipeline contains `carve`, nyxloom owns exactly one **logical carver session** at a time. “Long-running” means the model-provider session is resumed across many bounded daemon-launched turns; it does not require a permanently resident child process. That distinction preserves the current detached-wrapper, crash-safe daemon model:

1. the daemon decides that a carver turn is due;
2. the wrapper acquires `<project>.strategic-carver`;
3. the daemon launches or resumes the recorded provider session for one bounded turn;
4. the turn emits a typed result and exits;
5. the provider session id remains durable for the next turn.

The session retains the project’s working strategic model across:

- headroom refill;
- `READY_TO_CARVE` re-scope;
- periodic test-health work;
- each newly recorded merge;
- human feature/plan intake;
- daemon restarts; and
- compaction cycles.

It is not the implementation reviewer. The independent reviewer still evaluates real git state, runs the declared gate, and alone controls `AWAITING_REVIEW -> MERGE_READY | REVIEW_REJECTED`.

### 1.2 Non-goals

- Do not put an LLM call inside `plan_project()`. It remains pure, deterministic, and free of filesystem, subprocess, storage, and model access.
- Do not make chat history workflow truth. Durable spine documents, decisions, handoffs, typed events, and state projections remain sufficient to cold-rebuild the carver’s working context.
- Do not let the carver transition task state directly. It proposes; the reconciler validates and acts.
- Do not make the carver review its own implementations or replace `review_independent`.
- Do not re-read the whole repository after each merge. The normal update path is a typed merge digest.
- Do not assume `/compact`, a provider endpoint, or any other compaction API before the sibling harness research resolves the mechanism.
- Do not force a carver session on `gated` or `lean` projects, which intentionally accept externally authored handoffs.
- Do not turn the stage registry into a user-defined flow language. The new behavior remains a code-backed stage plus typed inputs/actions.

### 1.3 Existing behavior being extended

The current implementation has useful pieces but not the target lifetime:

- `stages.py` declares `carve` as serial and gives it only `spine-digest`; `frontier_review` has `session-reuse` plus `spine-digest`.
- `reconcile.plan_project()` is the pure scheduler. Its headroom, test-health, and `READY_TO_CARVE` branches all emit the same `CarveDispatch`, with a shared one-carve-per-pass guard.
- `daemon._execute_carve_dispatch()` creates a new synthetic `carve-<project>-<seq>` task, a new attempt, a new worktree under branch authority, and always calls `adapters.build_dispatch()`.
- the wrapper already holds `<project>.strategic-carver` for a carver process’s entire lifetime. This is the real single-writer protection; the planner’s in-flight scan is only an optimization.
- B6/P74 points carve and review packets at `<reports_dir>/SPINE-DIGEST.md`. The carver is told to maintain that digest, but every carve still starts cold.
- reviewer reuse already demonstrates the launch/resume pattern: `LaunchReview.resume_session` selects the most recent exited review attempt and the daemon calls `build_resume()` with a fresh attempt binding.
- `MERGE_RECORDED` already exists on manual and automatic merge paths and carries `merge_commit`, changed files in `progress_units`, and `source_kind`.
- `intake_chat.py` owns a separate resumable, read-only session that emits `PRODUCT_CALL` or `BRIEF`; it calls `decisions.open_decision()` and `backlog_items.create()`.
- `docs/routing-model-redesign.md` D-R10/D-R11 sketches a persistent carver tied to a still-live carve branch and assumes an external `/compact` turn. This plan supersedes the branch-scoped lifetime: the strategic session is project-scoped and survives admission of any particular carve branch. D-R11 becomes an interface requirement pending the sibling harness research, not an assumed API.

## 2. Architecture and source-of-truth model

### 2.1 One cognitive session, many typed turns

The daemon addresses the carver by `(project_id, generation, session_id)`. A generation is one continuous provider session. Ordinary work never invents another carver identity:

```text
durable direction + state
         |
         v
 COLD bootstrap turn ----capture----> generation 1 / session S1 / WARM
                                      |       |       |
                                  merge feed  |   human intake
                                              |
                                           carve turn
                                              |
                                    compaction maintenance turn
                                              |
                                      resume same S1 (or S2 if
                                      the harness rotates the id)
```

The carver has turn modes, not separate roles:

- `bootstrap`: load durable strategic truth and establish a checkpoint;
- `merge-feed`: incorporate one or more pending merge digests;
- `carve`: author handoff candidates for headroom, re-scope, targeted intake, or test health;
- `human-intake`: discuss a raw idea/plan and classify its disposition;
- `repair-proposal`: correct a schema/lint-invalid prior output;
- `compact`: maintenance requested by the daemon, with no product action.

All modes use the same recorded provider session. Every mode uses the same strategic-carver lease. Only `carve`/`repair-proposal` turns receive handoff-authoring write authority; feed, bootstrap, intake, and compaction are read-only.

### 2.2 Durable session projection

Add a focused module, proposed as `src/nyxloom/carver_session.py`, that owns serialization and reconstruction of:

```text
CarverSessionSnapshot
  project: str
  generation: int
  status: ABSENT | COLD | STARTING | WARM | COMPACTING | DEGRADED | ROTATING
  session_id: str | None
  route: immutable Route snapshot | None
  opened_at: timestamp | None
  last_success_at: timestamp | None
  last_turn_sequence: int | None
  last_consumed_event_sequence: int
  last_proposal_id: str | None
  spine_revisions: {north_star, product_definition, roadmap, backlog -> sha256}
  successful_turns_since_compaction: int
  measured_context_tokens: int | None
  measured_context_ratio: float | None
  resume_failures: int
  last_compaction_at: timestamp | None
```

The append-only project event log remains authoritative. A small
`<state-volume>/<project>/carver/session.json` may be maintained as an atomic,
rebuildable projection for cheap daemon reads, just as task statefiles project
task events. This follows `docs/ARCHITECTURE.md`/`docs/SPEC.md`: daemon
residency is an optimization, the state volume is authoritative, and a restart
loses no workflow fact. `doctor --rebuild` must reproduce the projection
byte-for-byte from events. Never place a raw provider transcript or session
secret in an event. The opaque session id is runtime state and is redacted from
notifications/dashboard views.

New audit events should be narrowly typed:

- `CARVER_SESSION_STARTED`: generation, opaque session id, route snapshot, spine revisions;
- `CARVER_SESSION_RESUMED`: generation, turn id/mode, source ids, route snapshot;
- `CARVER_CONTEXT_CONSUMED`: generation, turn id, highest source event sequence, digest/intake ids;
- `CARVER_PROPOSAL_RECORDED`: proposal id, generation, source ids, artifact paths/hashes, disposition enums;
- `CARVER_COMPACTION_REQUESTED`: generation and typed trigger;
- `CARVER_COMPACTION_FINISHED`: old/new generation/session identity if applicable, retained-reference acknowledgement, usage before/after where measurable;
- `CARVER_SESSION_ROTATED`: old/new generation and a fixed reason enum;
- `CARVER_SESSION_DEGRADED`: fixed failure class and retry count.

These events have no `TaskStateFile` effect and must be explicitly registered
as audit-only in the event-type closure invariant. The separate session
projector consumes them. A proposal that creates tasks still results in normal
`TASK_CREATED` and transition events; session events never bypass the task
state machine.

### 2.3 Stable session versus ephemeral worktree

The session identity is project-scoped, not branch-scoped. Each turn supplies
the current mode and working directory:

- read-only turns run at `cfg.root`;
- a branch-authority authoring turn runs in that carve’s worktree;
- main/files authority retains its current cwd behavior.

This requires a route capability test proving that session resume remains
correct when the resumed process has a different cwd/worktree. `RouteDef`
already has `resume`, `session_capture`, and worktree substitution for some
adapters, but support is not uniform. A route is eligible for `carve-3` session
reuse only when an adapter fixture proves:

1. launch in project A/root captures S;
2. resume S in a carve worktree sees the retained strategic context;
3. the turn reads/writes only within its declared permission/cwd boundary; and
4. the resulting session identity is recaptured.

If the active route cannot resume across cwd, the implementation must use a
stable, project-scoped planning worktree and move validated artifacts through a
daemon-controlled materialization step. It must not silently fall back to a
fresh session for every branch while claiming the feature is warm.

### 2.4 Cold, warm, degraded, and rotated

- **ABSENT/COLD:** no successful session generation exists, the configured
  route lacks a valid captured session, or a prior generation has been
  explicitly rotated. The next eligible turn is `bootstrap`.
- **STARTING:** a bootstrap wrapper has been launched but no session id has
  been captured yet. The planner emits no second carver turn.
- **WARM:** a session id is durable, its route snapshot is resumable, the last
  turn completed successfully, and no rotation condition is active.
- **COMPACTING:** a compaction maintenance turn is in flight. All intake/feed/
  carve work waits.
- **DEGRADED:** resume failed or the provider session is unavailable, but the
  bounded recovery budget is not exhausted. Pending inputs remain durable.
- **ROTATING:** the daemon has decided the current generation cannot be
  recovered. The next turn cold-bootstraps a new generation from durable
  truth.

A daemon restart changes none of these states; it rebuilds the snapshot and
continues. A provider or model change is not an in-place resume: because a
session’s assumptions and token accounting are route-specific, it rotates the
generation. A transient provider failure follows existing backoff/provider
pause behavior and does not consume or discard pending carver inputs.

### 2.5 Bootstrap context: durable truth, not a repository crawl

The cold bootstrap packet names and requires the carver to read:

1. configured spine levels 1–4:
   `north_star`, `product_definition`, `roadmap`, `backlog`;
2. `decisions_inbox`, retaining every OPEN/DISCUSSING decision and its resume
   prompt;
3. current non-terminal task summaries from statefiles: id, state, handoff
   path, dependencies, mutexes, input revision, and latest typed blocker;
4. the most recent configurable number of durable merge digests (default 10);
5. `reference/AUTHORING.md`, then any project sibling, because the carver
   authors handoffs;
6. project-declared `[refs]` by pointer where needed.

Bootstrap does not recursively inspect the repository. The project’s files are
still available for a targeted premise check while authoring a specific
handoff, as required by `reference/DOCTRINE.md` §3a; that check is scoped to
the exact files the candidate would touch and its verifying revision.

`SPINE-DIGEST.md` becomes a compatibility/inspection artifact, not the primary
memory mechanism. During migration it may be generated from the session
checkpoint for reviewers and cold fallback, but the warm carver no longer
re-reads it on every turn or owns it as an ad-hoc second source of truth.

Spine write authority remains explicit. `spine_writer.py` is currently a pure
emitter of already-decided structured `Feature`, `Milestone`, and `BacklogItem`
content; it does not decide what the spine should say. A carver may propose a
typed spine delta or a D-NNN question, but neither a normal feed nor a
compaction turn may overwrite spine documents. Any accepted structured update
goes through the existing writer/lint discipline (or a later dedicated delta
writer) and its resulting merge is then fed back as a normal `spine_delta`.

## 3. Merge-digest context feed

### 3.1 Typed digest

Both `cmd_merge()` and `_execute_auto_merge()` already emit
`MERGE_RECORDED`; centralize their payload construction in a helper (proposed
`src/nyxloom/merge_digest.py`) so manual and automatic merge semantics cannot
drift. Extend `MERGE_RECORDED` additively with:

```json
{
  "merge_commit": "<full sha>",
  "source_kind": "review",
  "progress_units": ["..."],
  "carver_digest": {
    "schema_version": 1,
    "digest_id": "merge:<project>:<event-sequence-or-commit>",
    "task_id": "<handoff id>",
    "merge_commit": "<full sha>",
    "first_parent": "<full sha>",
    "handoff": {
      "path": "<trove-relative path>",
      "sha256": "<content hash>",
      "title": "<frontmatter title>",
      "input_revision": "<carved base>"
    },
    "files": [
      {"path": "<repo-relative path>", "change": "A|M|D|R"}
    ],
    "diffstat": {
      "files_changed": 0,
      "insertions": 0,
      "deletions": 0
    },
    "review": {
      "verdict": "approved",
      "attempt_id": "<bound review attempt id>",
      "report_path": "<repo-relative path or null>",
      "report_sha256": "<hash or null>"
    },
    "spine_delta": {
      "changed": false,
      "paths": [],
      "before_revisions": {},
      "after_revisions": {},
      "changed_ids": {
        "features": [],
        "milestones": [],
        "backlog_items": []
      }
    }
  }
}
```

“What shipped” is represented by the task/handoff identity, title, immutable
handoff hash, merge commit, and exact changed-file/diffstat facts. Do not ask a
second LLM to summarize the diff. The carver already remembers why it authored
the handoff; a cold recovery can read that one hashed handoff, not the whole
repo. If future evidence shows a prose summary is necessary, add it as a
bounded, hashed artifact reference, not an unbounded field in the event.

The file list comes from `git diff-tree` against the first parent with
name-status and rename detection. The existing `progress_units` consumer keeps
working. `spine_delta` is computed mechanically only for the four configured
spine paths: hash before/after, parse already schema-validated frontmatter, and
set-diff feature/milestone/backlog ids. North-star body changes are represented
by the revision change and path; the daemon does not interpret prose.

### 3.2 Delivery and acknowledgement

`_build_input()` scans `MERGE_RECORDED` after
`CarverSessionSnapshot.last_consumed_event_sequence` and supplies bounded
`pending_carver_feeds` to `ReconcileInput`. `plan_project()` emits one
`ResumeCarverSession(mode="merge-feed", source_sequences=[...])` when:

- the pipeline contains `carve`;
- the project is not `drain-agents`;
- no carver turn is in flight;
- a resumable or bootstrappable carver route exists;
- budget/admission allows a model turn; and
- pending merge digests exist.

Batch digests that accumulated in one pass, preserving event order, up to a
configured prompt-size bound. This still feeds every merge; it avoids launching
three near-simultaneous provider turns when three serial merge events are
already durable before the next reconcile tick.

The resume prompt is a fixed template plus canonical JSON. The carver returns:

```json
{
  "kind": "context-ack",
  "schema_version": 1,
  "turn_id": "<daemon-issued>",
  "consumed_digest_ids": ["..."],
  "spine_revisions": {"...": "..."},
  "new_risks": [{"key": "<short-id>", "disposition": "remember|decision|backlog"}]
}
```

The daemon advances the event cursor only after the schema validates and the
ack includes every digest id in that turn. If the wrapper or parser fails, the
cursor does not move and the same idempotent digest batch is delivered again.
This is **at-least-once delivery with idempotent ids**, not a fragile claim of
exactly once. Duplicate delivery is harmless because the prompt says digest ids
are facts, not instructions, and the session acknowledges a set.

### 3.3 Ordering with normal carving

One carver turn is planned per project per pass. Recommended priority:

1. finish/repair an already-produced proposal;
2. bootstrap or recover the session;
3. ingest pending merge digests;
4. perform due compaction;
5. handle a `READY_TO_CARVE` re-scope;
6. handle queued human intake;
7. run due test-health work;
8. refill ordinary headroom.

This makes “what exists now” current before the next new handoff is authored.
Re-scope remains ahead of brand-new headroom, as it is today, but never before a
pending merge feed that may invalidate its premise.

## 4. Typed carve-proposal contract and deterministic reconciliation

### 4.1 Output envelope

Replace the current `CARVE-<seq>.md` “summary plus discover whatever files
appeared” contract with a schema-backed `CarverTurnResult`. The result points
to canonical handoff files rather than duplicating their frontmatter:

```json
{
  "kind": "carve-proposal",
  "schema_version": 1,
  "proposal_id": "<project>:carve:<generation>:<turn-id>",
  "turn_id": "<daemon-issued>",
  "source": {
    "mode": "headroom|rescope|targeted-intake|test-health|plan",
    "refs": ["B27", "demo-P12", "intake-abc", "plan-X"],
    "base_revision": "<full default-branch sha>",
    "merge_digest_cursor": 1234
  },
  "artifacts": [
    {
      "kind": "handoff",
      "path": "nyxloom-trove/handoffs/<id>.md",
      "sha256": "<hash>",
      "source_ref": "<backlog/roadmap/review/intake ref>"
    }
  ],
  "dispositions": [
    {
      "source_ref": "<ref>",
      "result": "handoff|decision|backlog|redundant|drop",
      "artifact_ref": "<path or null>",
      "reason_code": "<closed enum>"
    }
  ],
  "outcome": "CANDIDATES_READY|MILESTONE_COMPLETE|ROADMAP_EXHAUSTED|SPEC_GAP|DECISION_REQUIRED|EXTERNAL_BLOCKER|BUDGET_EXHAUSTED",
  "headroom_estimate": 0
}
```

The handoff remains the only machine representation of its contract, satisfying
`docs/SPEC.md` §2. The proposal carries only artifact identity/hash and
admission/disposition metadata. For every handoff artifact the daemon:

- resolves the path under configured handoff globs;
- rejects traversal and unexpected paths;
- verifies the content hash;
- parses canonical frontmatter;
- runs `nyxloom lint`;
- verifies `input_revision == source.base_revision`;
- verifies every oracle is satisfiable within `scope.touch` or has a named
  mechanical escalation;
- records the proposal only when all referenced artifacts are present and
  valid.

Invalid output creates a bounded repair input for the same warm session. It
does not create a task, overwrite the source proposal, or ask a human to repair
frontmatter. After the configured repair count, emit a typed
`NEEDS_OPERATOR{reason: "carver-proposal-invalid"}` and preserve the artifacts.

### 4.2 Input to `plan_project()`

Add pure snapshot fields:

```text
ReconcileInput.carver_session: CarverSessionView
ReconcileInput.pending_carver_feeds: tuple[CarverFeed]
ReconcileInput.pending_human_intakes: tuple[HumanIntake]
ReconcileInput.validated_carve_proposals: tuple[ValidatedCarveProposal]
```

The view contains only enums, ids, counters, booleans, hashes, and bounded
typed records. No raw plan, intake, review, or handoff prose enters planner
actions.

Add code-backed actions:

- `StartCarverSession(mode, source_ids)`;
- `ResumeCarverSession(mode, source_ids, generation)`;
- `CompactCarverSession(generation, trigger)`;
- `AdmitCarveProposal(proposal_id, artifact_ids)`.

`plan_project()` continues to return the existing list-compatible
`PlanResult`; the new actions are ordinary members and its pure
`ReconcileTrace` gains only short enum/id breadcrumbs such as
`carver:merge-feed` or `carver:compact:size`. No session id, prompt prose, or
handoff body enters the trace.

`CarveDispatch` can be retained as a migration alias that normalizes to
`Start/ResumeCarverSession(mode="carve")`; after compatibility is removed,
delete it rather than maintaining two execution paths.

For each validated proposal, `plan_project()` deterministically sorts artifact
ids and emits admission once. `AdmitCarveProposal` atomically:

1. rechecks proposal/artifact hashes at the effect boundary;
2. emits `CARVER_PROPOSAL_ADMITTED`;
3. creates normal tasks using the already-parsed `Frontmatter` and paths;
4. performs the same re-scope `TASK_SUPERSEDED{outcome: RESCOPED}` only after
   replacement proposal admission, not merely after launching a carve;
5. marks the proposal cursor consumed.

This tightens current B7 atomicity. Today the original task is superseded once
the re-scope agent launches, even if it later produces no valid replacement.
Under the proposal protocol, supersession waits until the replacement is
validated/admitted or the carver explicitly returns a typed `drop|decision`
disposition.

### 4.3 No nondeterminism in transitions

The LLM is upstream of the state machine:

```text
untrusted model output
  -> JSON-schema parse
  -> artifact path/hash/lint/revision checks
  -> ValidatedCarveProposal snapshot
  -> pure plan_project action
  -> effect-boundary recheck
  -> ordinary task/decision/backlog events
```

The daemon, not the model, allocates task event sequences and D-NNN ids. The
model may propose a product question but cannot choose the final decision id.
The daemon, not the model, decides whether a proposal was schema-valid,
whether a hash matches, whether a transition is legal, or whether admission
caps/mutexes permit dispatch. Replaying the same event/input snapshot yields
the same ordered actions.

## 5. Session lifecycle and lease interaction

### 5.1 Start

When a project with a `carve` stage has work requiring the carver and no
session exists, the pure planner emits `StartCarverSession`. At execution:

1. recheck pause, budget, route capability, and absence of a live turn;
2. create a normal synthetic carver attempt/turn record;
3. build the bootstrap packet;
4. launch through the wrapper with
   `leases=[{"name": "<project>.strategic-carver", "capacity": 1}]`;
5. capture the provider session id using the route’s declared mechanism;
6. validate the bootstrap checkpoint/ack;
7. append `CARVER_SESSION_STARTED` and mark WARM.

If session capture fails, the attempt is visible and the session remains COLD
or DEGRADED. Never record “warm” based only on process exit.

### 5.2 Resume

Every subsequent turn calls `adapters.build_resume(route, session=..., worktree=..., prompt=...)`.
It still mints a fresh nyxloom attempt/turn id, route snapshot, receipt, and
usage record. The persistent provider session is a cache/context optimization;
it is never reused as an evidence identity. Each output must bind to the new
daemon-issued `turn_id`, mirroring reviewer verdict-attempt binding.

The session’s route is pinned for its generation. Availability probes may
pause it transiently. A deliberate route/model/config change rotates rather
than attempting a cross-model resume.

### 5.3 Lease

Keep `leases.py` unchanged. Its flock semantics are already the right
mechanism:

- every bootstrap/resume/feed/intake/repair/compaction wrapper requests the
  same `<project>.strategic-carver` exclusive lease;
- the wrapper holds the lock for the entire provider process;
- a racing targeted intake and automatic headroom turn yields one winner; the
  loser records `lease-lost-race` and retries from durable input;
- process death releases the lease in the kernel;
- no stale-lock-breaking protocol is added.

The session id is not itself a lock. Planner checks (`status` and in-flight
turn) reduce races, while flock is the effect-boundary authority. Lease
contention must not create a new session generation or advance any feed/intake
cursor.

### 5.4 Rotation and cold recovery

Rotate only on typed conditions:

- bounded consecutive resume failures;
- provider reports the session missing/expired;
- route/model/CLI identity changed incompatibly;
- compaction driver reports unrecoverable failure;
- operator explicitly requests rotation;
- session checkpoint and durable spine revisions cannot be reconciled.

Cold recovery reads the four spine files, open decisions, current work-state,
recent merge digests, and AUTHORING doctrine. It does not replay every old
conversation or scan every source file. Pending feeds/intakes are cursor-based
and survive rotation.

## 6. External compaction protocol

### 6.1 Open dependency and driver boundary

The sibling research task owns the exact externally-triggered compaction
mechanism. Define an adapter-facing capability without choosing its
implementation:

```text
CompactionDriver.request(
  route_snapshot,
  session_id,
  retention_prompt,
  worktree,
  turn_id
) -> CompactionResult(
  status,
  resulting_session_id,
  usage,
  acknowledgement
)
```

The implementation may ultimately be a special resume prompt, a CLI command,
or a provider API. No package may hardcode `/compact` until the research
result proves that contract for supported adapters. A route without a proven
driver remains eligible for bounded pilot sessions only; production enablement
of the long-running feature requires either a compaction driver or an approved
rotation fallback decision.

### 6.2 Trigger policy

Evaluate between turns, never while a carver wrapper holds the lease.
Recommended defaults:

- size trigger: measured context use at or above 70% of the route/model
  context window;
- turn trigger: 24 successful non-compaction turns since the last compaction;
- hard fallback: 32 turns when usage is unknown or untrusted;
- operator trigger: audited immediate request;
- drift trigger: checkpoint no longer names the current revisions of two or
  more spine documents.

Use usage only when the route’s `usage_source` is verified. `cached_in` is
valuable evidence of reuse but is not itself current context size. If the
harness exposes no trustworthy context occupancy, rely on the turn threshold.

When a trigger and ordinary work are both pending, compact before the next
ordinary turn after all earlier feed acknowledgements are durable. New merge
events remain queued and are delivered after compaction.

### 6.3 Retention template

The daemon supplies a versioned, fixed retention contract:

```text
You are performing externally requested context maintenance for the persistent
strategic CARVER. Do not carve, review, edit files, open decisions, or change
workflow state.

KEEP:
1. The current north-star, product-definition feature/acceptance ids, roadmap
   milestones, and backlog ids, with the supplied spine revision hashes.
2. Every OPEN/DISCUSSING D-NNN question and resume prompt.
3. Current non-terminal task ids, states, dependencies, mutex/conflict
   assumptions, and unresolved blockers.
4. The most recent 10 acknowledged merge digests, especially their changed
   files and spine deltas.
5. Unresolved risks, product assumptions, pending human intake, and proposals
   awaiting repair/admission.
6. The AUTHORING constraints: strong explicit contract, exact context pointers,
   behavioral oracle + negative + real gate, scope.touch/forbid, mechanical
   BLOCKED, and product calls as D-NNN decisions.

DROP:
1. Resolved carve deliberation and superseded candidate drafts.
2. Old tool output, full diffs, duplicate explanations, and repo exploration
   whose conclusion is already represented by a retained fact.
3. Completed intake chit-chat after its proposal/decision/backlog disposition
   is durable.
4. Merge details older than the retained window unless they support an
   unresolved risk or standing invariant.

Retain ids and revision hashes exactly. Return only the requested typed
compaction acknowledgement.
```

The recent-merge count is configurable but bounded. The four spine files are
the durable ground truth; the session is only working context. Therefore a
fact that was improperly dropped can be restored by a targeted spine/checkpoint
read, and a suspect generation can be rotated.

### 6.4 Verification and failure

The acknowledgement must identify:

- turn id and old/new session identity;
- spine revision hashes retained;
- open decision ids retained;
- recent merge digest ids retained;
- pending proposal/intake ids retained;
- unresolved-risk keys retained.

The daemon compares these sets to its durable snapshot. A missing required id
is not “probably fine”: compaction fails closed, the old cursor does not
advance, and policy chooses retry or cold rotation. The daemon never infers
success from a zero exit code alone.

## 7. Independent review and model/tier policy

### 7.1 Rename and separation

Rename the stage kind `frontier_review` to `review_independent`. This is a
semantic correction: “frontier” describes a route choice, while independence
is the invariant. Keep the serialized `Role.FRONTIER_REVIEW` value initially
to avoid an event/state schema migration; stage naming and runtime role naming
need not change in the same package.

`compose()` should normalize legacy `frontier_review` to
`review_independent` with an explicit deprecation trace. Presets become:

```text
full:  carve -> implement -> self_review -> review_independent -> triage -> auto_merge -> post_merge_gate
gated: implement -> self_review -> review_independent -> triage -> auto_merge -> post_merge_gate
lean:  implement -> self_review -> review_independent -> triage -> auto_merge
```

Reject a pipeline spelling both aliases. Context reuse for the reviewer remains
its own session pool; it never receives the carver session id and never emits a
carve proposal.

### 7.2 Recommended carver tier

Add/use `carve-3`, resolved only to routes approved for highest-band
reasoning/agentic work, resumability, measured usage, and compaction support.
Initial recommendation: GPT-5.6 sol at high effort, matching the judgment level
used to author this plan. Snapshot the actual route on every turn.

Carving is the highest-judgment role because a mistake propagates:

- it chooses whether work exists at all;
- it reconciles north-star, roadmap, backlog, decisions, and recent reality;
- it chooses package boundaries, dependencies, scope, tier, and behavioral
  oracles;
- it influences several downstream implementation/review turns; and
- persistent context makes its assumptions long-lived.

The usual “cheap planner, expensive reviewer” allocation is inverted here on
purpose. A reviewer sees a bounded handoff and diff, has a deterministic gate,
and can fail closed. The carver must reason across the whole work system and
prevent bad work from entering it. Spend the strongest judgment before
multiplying the error.

This does **not** justify an underpowered reviewer. `review_independent` remains
strictly more capable than the implementation route on the relevant capability
axes. Ordinary `implement-1` work can use `review-2`; highest-band or
cross-cutting work uses `review-3`. In practice the reviewer may be cheaper
than `carve-3` for most packages, but never below the capability-matching
invariant in the north-star/product definition.

## 8. Human-intake path

### 8.1 Reuse the same strategic identity

Keep `intake_chat.py` as the transport, redaction, transcript, confirm/finalize,
and HTTP/ntfy bridge, but remove its ownership of an independent
`IntakeChat.session_id`. Instead it writes a typed `HUMAN_INTAKE_QUEUED` event
and addresses the project’s `CarverSessionSnapshot`.

One conversation can span multiple human turns. Each turn records:

```text
HumanIntake
  intake_id
  turn_id
  source: ui | ntfy | cli | plan-inbox
  redacted_user_text
  prior_disposition_refs
  status: QUEUED | IN_FLIGHT | AWAITING_HUMAN | DISPOSED
```

The carver receives current work-state implicitly from its warm context plus a
small typed delta (new tasks/merges/decisions since the prior intake turn).
This is the core benefit over today’s cold/sibling intake agent: a new idea is
evaluated against what is already open, recently merged, or deliberately
deferred.

### 8.2 Permission boundary

Human text is untrusted. Intake turns resume the same cognitive session but
run read-only (`Read/Grep/Glob`, no edit/write/bash) and may emit only:

```json
{
  "kind": "intake-response",
  "schema_version": 1,
  "intake_id": "...",
  "turn_id": "...",
  "reply": "<redacted bounded user-facing response>",
  "status": "ask-human|ready",
  "disposition": {
    "kind": "carve-proposal|decision|backlog|redundant",
    "payload": {}
  }
}
```

On `ask-human`, persist the reply and wait. On `ready`:

- `carve-proposal`: enqueue a separate authoring turn with a source ref to the
  intake; do not grant the interactive turn write permission;
- `decision`: call `decisions.open_decision(cfg, question, resume_prompt,
  raised_by="carver-intake")`; the daemon allocates the D-NNN and the intake
  waits on it;
- `backlog`: call `backlog_items.create()` with the distilled brief and linked
  decisions;
- `redundant`: record the merged/task/decision refs proving it and close the
  intake without manufacturing work.

This subsumes today’s `PRODUCT_CALL`/`BRIEF` semantics while retaining their
proven persistence helpers. A compatibility parser can accept the old text
markers during migration, but new carver turns use only the typed envelope.

### 8.3 Plan intake

`docs/plan-plan-intake.md` remains compatible. A dropped plan is a larger
human-intake source:

1. register the plan and pass its path/hash, not its whole prose, to the warm
   carver;
2. reconcile it against retained spine/decisions/current work;
3. emit D-NNN requests for direction conflicts via the same decision path;
4. return `backlog`, `redundant`, or a linked typed carve proposal;
5. let `plan_project()` admit the resulting handoffs mechanically.

The persistent carver is therefore the shared reasoning surface for raw ideas,
briefed backlog items, and plans; there are not three strategic agents with
diverging pictures of the project.

## 9. Configuration and compatibility

Proposed project policy, default-off until rollout:

```toml
[stage.carve]
concurrency = "serial"
tier = "carve-3"
session = "project-persistent"
compact_context_ratio = 0.70
compact_after_turns = 24
compact_hard_after_turns = 32
retain_merge_digests = 10
max_resume_failures = 2
max_proposal_repairs = 2
```

Only expose keys supported by the final compaction research result. Schema and
load-time validation reject `session = "project-persistent"` when:

- the pipeline lacks `carve`;
- no `carve-3` route is marked role-eligible/resumable;
- thresholds are nonsensical; or
- production mode requires compaction but the selected route has no driver.

For projects with no explicit setting:

- during shadow migration, retain current fresh `CarveDispatch` behavior;
- after dogfood acceptance, `full` may default to persistent;
- `gated` and `lean` never start, feed, compact, or display a carver session;
- external handoff discovery remains unchanged for carve-less pipelines;
- triage in carve-less pipelines still routes architectural/stale/exhausted
  work to `NEEDS_DECISION`, as current code does.

## 10. Observability and operational behavior

Dashboard/doctor should show typed, non-secret facts:

- carver status and generation;
- warm/cold/compacting/degraded;
- pinned route/model/effort;
- last successful turn and mode;
- pending merge/intake counts;
- last consumed event sequence;
- turns/context ratio since compaction;
- last compaction result;
- proposal validation/admission status;
- lease holder metadata from `leases.holder_info()`.

Never render the raw session id, raw user prompt, raw compaction transcript, or
unbounded model rationale. Logs use `event_type=`, never structlog’s reserved
`event=`/`level=` keys. Agent prompt additions must respect
`adapters.build_dispatch`’s hard argv budget: point to packet files and put
substance there; keep resume prompts bounded and test realistic deep paths.

The event log provides the audit questions a human needs:

- Which merge digests had the carver consumed when it authored P123?
- Was the session warm or cold?
- Which spine revisions did it claim to hold?
- Did compaction happen between intake and carve?
- Which typed proposal produced each task?
- Was a lease race or resume failure involved?

## 11. Failure and safety matrix

| Failure | Required behavior |
|---|---|
| daemon restarts | rebuild session projection; resume same generation; no duplicate task/proposal |
| provider session missing | mark DEGRADED, bounded retry, then rotate; cold bootstrap from durable truth |
| merge feed delivered twice | carver idempotently acknowledges digest id; cursor advances once |
| merge feed parse/ack fails | cursor stays; retry same bounded batch; no carve runs ahead of it |
| proposal JSON malformed | no task creation; warm repair turn; bounded escalation |
| handoff hash/lint/revision fails | no admission; exact finding fed to repair; source artifact preserved |
| compaction omits retained id | fail closed; no success event/cursor move; retry or rotate |
| lease contention | loser launches no real model process, consumes no input, retries next pass |
| carver session unavailable | implementation/review pipeline continues for already-carved tasks; new carving/intake waits visibly |
| spine changes outside merge path | revision mismatch triggers targeted spine refresh/compaction or rotation before next carve |
| manual merge omits normal helper | doctor flags legacy `MERGE_RECORDED` without digest; synthesize once from commit or require operator repair |
| carve-less preset | no carver action or session state is required; external handoffs continue |
| reviewer route is cheaper than carver | allowed only if still capability-matched above implementer; independence and real gate remain |

## 12. Phased implementation — six buildable packages

Every implementation handoff must follow `reference/AUTHORING.md`: exact
context pointers, numbered work, explicit `scope.touch/forbid`, behavioral
oracle with negative, and mechanical BLOCKED. The authoritative gate for
nyxloom is `[gates.tester-unified]` from `nyxloom-trove/nyxloom.toml`:

```text
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python \
  -m coverage run --source=src/nyxloom -m pytest tests -q && \
  PYTHONPATH=src /opt/tester-venv/bin/python -m coverage json \
  -o /tmp/nyxloom-cov.json && PYTHONPATH=src /opt/tester-venv/bin/python \
  -m nyxloom.coverage_gate --base main \
  --coverage-json /tmp/nyxloom-cov.json --source src/nyxloom'
```

Run gates serially. `{worktree}` is resolved by the daemon in the real gate
environment, never replaced with a cockpit venv command.

### Package 1 — contracts, events, projection, and stage naming

**Purpose.** Establish schemas and durable vocabulary without launching a
persistent session.

**Likely scope.** New `carver_session.py`; `types.py` event additions;
event/session JSON schemas; `storage.py`/SQLite audit handling and rebuild;
`stages.py`; `config.py`; focused type/stage/storage tests.

**Work.**

1. Define `CarverSessionSnapshot`, `MergeDigest`, `CarverTurnResult`,
   `ValidatedCarveProposal`, and intake/feed view types.
2. Add session/proposal/compaction audit events and a rebuildable session
   projection.
3. Rename the stage to `review_independent`, with legacy normalization and
   preset parity.
4. Add configuration fields behind a default-off feature setting.
5. Extend event-type and role/stage closure tests.

**Oracles.**

- Replaying a mixed session event stream yields a byte-identical projection;
  deleting the projection and rebuilding restores cursor/generation/status.
  Negative: an unknown or missing event handler makes the event-closure test
  fail.
- `full`, `gated`, and `lean` normalize to the expected canonical pipelines;
  specifying both review names is rejected. Negative: `gated`/`lean` produce
  no implicit `carve`.
- Schema rejects path traversal, duplicate artifact ids, unknown dispositions,
  missing turn binding, and an invalid compaction threshold.

**Gate.** Full `tester-unified` command above.

**Frozen-core adjacency.** **High.** It touches current frozen/core surfaces
`types.py`, `config.py`, event schemas, and stage composition. Explicitly thaw
and serialize this package; no parallel package may own those files.

### Package 2 — deterministic planner actions and merge-digest production

**Purpose.** Put all new nondeterministic observations into typed
`ReconcileInput` fields and make `plan_project()` schedule them mechanically.

**Likely scope.** New `merge_digest.py`; `reconcile.py`; daemon input builder;
`cli.py` manual merge; daemon automatic merge; merge/reconcile tests.

**Work.**

1. Make manual/automatic merge call one digest builder and emit payload parity.
2. Derive pending feed/proposal/intake snapshots from event cursors.
3. Add start/resume/compact/admit actions and the one-turn priority order.
4. Preserve existing pause, budget, route, re-scope priority, and one-carver
   constraints.
5. Keep carve-less action plans byte-identical.

**Oracles.**

- Given the same merge commit, manual and automatic paths emit identical
  `carver_digest` fields. Negative: a renamed/deleted file or spine id change
  omitted from the digest fails.
- Property test: identical `ReconcileInput` produces identical ordered actions
  and never more than one carver turn. Negative: pending merge feed plus
  headroom must schedule feed, not carve.
- Pipeline matrix: `full` plans session work; `gated`/`lean` never do, while
  their external handoff lifecycle remains unchanged.

**Gate.** Full `tester-unified`.

**Frozen-core adjacency.** **High.** `reconcile.py` is the deterministic core;
`cli.py` and daemon merge paths are dual authorities that must land together.

### Package 3 — persistent bootstrap/resume executor and proposal admission

**Purpose.** Replace fresh carve launches with the durable session executor,
while compaction remains disabled.

**Likely scope.** `daemon.py`, `adapters.py`, `wrapper.py` only if a generic
turn binding is needed, `carver_session.py`, proposal schemas/parser, fake-agent
behavior, carver/daemon/adapter tests.

**Work.**

1. Implement bootstrap, session capture, warm resume, turn-id binding, usage,
   and route pinning.
2. Route every carver turn through the existing strategic-carver wrapper lease.
3. Build mode-specific packet files within argv headroom.
4. Validate proposal envelope, handoff hashes/frontmatter/lint/revision.
5. Admit validated proposals atomically and move re-scope supersession to
   successful admission/disposition.
6. Implement bounded resume recovery and generation rotation.

**Oracles.**

- End-to-end fake: bootstrap captures S1; second carve and a feed resume S1;
  daemon restart still resumes S1. Negative: capture failure never records
  WARM.
- Two concurrent targeted/automatic turns: exactly one fake model starts; the
  loser advances no cursor and creates no generation.
- Cross-worktree fixture proves retained context and permission boundary.
  Negative: an adapter lacking this capability is ineligible rather than
  silently cold-started.
- Malformed JSON, stale base revision, hash mismatch, lint-red handoff, or
  wrong turn id creates zero tasks. A repaired, valid proposal creates each
  task once.
- Re-scope origin remains `READY_TO_CARVE` until replacement admission or an
  explicit typed decision/drop disposition.

**Gate.** Full `tester-unified`.

**Frozen-core adjacency.** **Medium-high.** It changes daemon launch/effect
boundaries and adapter argv behavior. Realistic deep-path argv tests and the
behavioral fake are mandatory.

### Package 4 — merge-feed loop and external compaction driver

**Purpose.** Keep working context current and bounded.

**Dependency.** The sibling compaction-harness research must provide a tested
driver contract or explicitly choose rotation fallback. This package may build
the interface/fake first but must not production-enable persistent sessions on
an assumed API.

**Likely scope.** `carver_session.py`, `daemon.py`, `adapters.py`, config/schema,
doctor, fake-agent and compaction/feed tests.

**Work.**

1. Implement at-least-once digest batching and acknowledgement cursor.
2. Implement size/turn/operator/drift triggers.
3. Integrate the research-selected `CompactionDriver`.
4. Validate retention acknowledgements and fail closed on missing facts.
5. Implement rotate-and-bootstrap fallback.

**Oracles.**

- Three merges before one tick are delivered in order in one bounded turn;
  partial ack advances none; retry consumes all once by id.
- Threshold matrix triggers at 70% or 24 turns, falls back at 32 unknown-usage
  turns, and never compacts while the lease is held.
- Compaction fake deliberately drops one OPEN decision/recent digest; daemon
  rejects success and rotates/retries. Positive retains all required ids and
  subsequent carve resumes the resulting session.
- Existing implementation/review tasks continue while the carver is degraded;
  new carve/intake work is visibly parked.

**Gate.** Full `tester-unified`.

**Frozen-core adjacency.** **Medium.** It affects daemon scheduling/adapters but
not task transitions. Config schema ownership still requires serialization.

### Package 5 — human/plan intake bridge into the carver

**Purpose.** Make the persistent carver the one human-facing strategic intake
identity.

**Likely scope.** `intake_chat.py`, `backlog_items.py` only if typed metadata is
missing, `decisions.py` call sites (not allocator semantics), daemon/API/CLI
bridge, plan-intake integration, intake tests.

**Work.**

1. Replace independent intake provider-session ownership with queued carver
   turns while preserving redaction/transcripts.
2. Add typed `ask-human|ready` and disposition parsing.
3. Route decisions through `open_decision`, backlog through `create`, and
   ready work through a separate authoring proposal turn.
4. Enforce read-only intake permissions even though the shared session can
   author during other modes.
5. Treat plan inbox entries as hashed intake sources.

**Oracles.**

- A new idea after merge M is handled in the same generation and the turn
  input cursor proves M was consumed first. Negative: no sibling intake session
  id is launched.
- Read-only intake cannot edit/write/bash; a `carve-proposal` disposition
  schedules a later write-authorized turn rather than mutating on the chat
  turn.
- Product question allocates one new D-NNN exactly once; incomplete aligned
  work creates one backlog item; ready work creates a typed proposal; redundant
  work creates neither.
- Existing redaction and duplicate-finalization tests remain green.

**Gate.** Full `tester-unified`.

**Frozen-core adjacency.** **Low-medium.** It uses existing decision/backlog
mechanics and should not change their core allocation formats.

### Package 6 — migration, shadowing, observability, and dogfood enablement

**Purpose.** Retire B6’s fresh digest as primary context without a flag day.

**Likely scope.** migration/helper module, doctor/render/dashboard, config
templates, docs/specs, compatibility tests, nyxloom’s own trove config only
after shadow evidence.

**Work.**

1. Shadow-build merge digests and session projections while fresh carves remain
   authoritative; compare candidate proposals without admitting shadow output.
2. Seed the first persistent generation from spine 1–4,
   `SPINE-DIGEST.md`, open decisions, active work, and recent merges.
3. Enable persistent mode for nyxloom only after cache/context/quality evidence.
4. Reclassify `SPINE-DIGEST.md` as generated compatibility/checkpoint output;
   stop requiring warm carver turns to read/maintain it.
5. Add dashboard/doctor views and operator rotation/compaction controls.
6. Remove the fresh path only after rollback and carve-less tests pass.

**Oracles.**

- Shadow mode mutates no task/backlog/decision state and can be disabled with
  no migration rollback.
- Warm second/third turns show a materially high cache-hit ratio (record the
  observed 95.8%–99.7% reviewer precedent as a benchmark, not a hard universal
  threshold) and correctly identify a synthetic cross-package conflict that a
  cold digest-only baseline misses.
- Restart, route rotation, compaction, and rollback drills lose no pending
  merge/intake/proposal ids.
- `gated`/`lean` byte-parity suite and external handoff admission remain green.
- Doctor reports missing digest, mismatched spine revision, stuck cursor,
  leaked second session, and long-held lease.

**Gate.** Full `tester-unified`, then one serialized dogfood cycle in the real
daemon/gate environment before default-on.

**Frozen-core adjacency.** **Medium.** Mostly migration/observability, but
removing compatibility code touches stage/daemon behavior and must be last.

## 13. Migration and rollout detail

1. **Land contracts default-off.** Existing B6/P74 behavior is unchanged.
2. **Emit merge digests in shadow.** No model calls; doctor verifies parity
   between manual and automatic merge.
3. **Shadow session.** Bootstrap/resume a non-authoritative carver that may
   produce proposals but cannot admit them. Compare conflict detection,
   handoff lint quality, cache use, and cost to the fresh baseline.
4. **Seed from current durable context.** Read spine 1–4, open decisions,
   active state, last 10 merge digests, and existing `SPINE-DIGEST.md` once.
5. **Enable nyxloom dogfood.** Keep a one-command audited rotate/disable path.
   Do not enable dstdns/topos or any paused project implicitly.
6. **Move intake.** Existing in-progress `IntakeChat` sessions finish on the
   legacy path; new intake ids use the carver. Do not attempt to merge two
   provider sessions.
7. **Retire primary digest dependency.** Reviewer may continue reading a
   generated digest pointer; warm carver does not.
8. **Change `full` default only after evidence.** `gated`/`lean` stay
   carve-less permanently unless their explicit pipeline changes.

Rollback sets the feature to fresh mode, stops scheduling new persistent
turns, and leaves all event/digest/proposal artifacts readable. It never deletes
the provider session or rewrites event history.

## 14. Risks and open D-NNN decision points

The ids below are plan-local placeholders. Before implementation, allocate the
real next ids through `decisions.open_decision()` so they cannot collide with
concurrent decisions.

### D-068 — Is session identity independent of carve branches?

**Recommendation: yes.** A project-scoped strategic identity must survive
branch admission to meet the feature. Require cross-worktree adapter proof or a
stable planning-worktree materialization fallback. Reject the old D-R10
branch-lifetime rule for this feature.

**Risk addressed:** tying identity to a branch recreates fresh context after
every accepted package and cannot serve human intake between carves.

### D-069 — Which external compaction harness contract is supported?

**Open dependency: sibling research.** Choose only after the research proves
session-id behavior, cwd behavior, output/ack shape, usage visibility, and
failure semantics for supported CLIs. Do not assume `/compact`.

**Risk addressed:** an imagined API can strand or silently replace the
provider session.

### D-070 — What rotates a single-point strategic session?

**Recommendation:** two consecutive genuine resume failures, confirmed session
missing, incompatible route change, failed compaction retention after one
retry, or explicit operator action. Preserve pending cursors across rotation.

**Risk addressed:** the session is a single point of context availability.
Mitigation is durable cold rebuild, not multiple concurrent carvers; multiple
writers would be worse than a visible pause.

### D-071 — What delivery guarantee does merge feeding claim?

**Recommendation: at-least-once with idempotent digest ids and all-or-nothing
batch acknowledgement.** Do not claim exactly-once across provider/process
failures.

**Risk addressed:** cursor advancement before real ingestion loses a merge;
advancement after a crash can redeliver. Idempotency safely handles the latter.

### D-072 — Can a human-intake turn mutate the repository?

**Recommendation: no.** Resume the same cognitive session in a read-only turn;
schedule a separately admitted authoring turn for ready work.

**Risk addressed:** user-controlled prose crossing directly into a write-capable
session is an injection and scope-escalation boundary.

### D-073 — Carver and reviewer tiers

**Recommendation:** `carve-3` = sol/high or another operator-approved top
reasoning/agentic route; `review_independent` is capability-matched above the
implementer and may usually be cheaper than the carver.

**Risk addressed:** a cheap strategic agent multiplies bad decomposition;
making every bounded review equally expensive wastes budget. The independent
gate and strict capability rule remain non-negotiable.

### D-074 — Stage rename compatibility window

**Recommendation:** normalize `frontier_review` for one deprecation release,
render only `review_independent`, reject both names together, then remove the
alias after registered projects migrate.

**Risk addressed:** a flag-day rename can make valid existing pipeline configs
unloadable.

### D-075 — How much merge detail is retained?

**Recommendation:** exact paths/name-status, mechanical diffstat, handoff
identity/hash/title, typed approved verdict/reference, and spine delta; no
LLM-generated prose summary. Retain 10 digests in working context, all events
durably.

**Risk addressed:** too little context misses cross-package conflicts; too much
diff/prose causes drift, prompt growth, and injection exposure.

### D-076 — What happens when compaction loses a required fact?

**Recommendation:** fail closed on acknowledgement-set mismatch, retry once,
then rotate and cold-bootstrap. Never let a “mostly retained” generation carve.

**Risk addressed:** silent fact loss is worse than a cold-cache cost because it
can misalign multiple downstream packages.

### D-077 — How is prolonged lease contention escalated?

**Recommendation:** ordinary contention retries without alarm; after a bounded
age (for example 2x the carver turn wall-clock cap), doctor/daemon emits one
deduplicated `NEEDS_OPERATOR{reason: "strategic-carver-lease-stuck"}` with
holder metadata. Never break a live flock.

**Risk addressed:** contention is expected briefly, but a wedged holder can
starve merge feeds and intake. Breaking locks would violate the existing lease
doctrine.

### D-078 — How is strategic drift measured?

**Recommendation:** checkpoint exact spine hashes and merge cursor on every
successful turn; before a carve, compare them with durable current values. A
mismatch schedules targeted refresh before authoring. Separately measure
proposal overlap/stale-premise/rejection rates over time.

**Risk addressed:** a warm session can be confidently stale. Warmth is useful
only when tied to revision/cursor evidence.

## 15. Acceptance of the complete feature

The feature is complete only when all of the following hold in the real
tester/daemon environment:

- exactly one project-scoped carver session generation is WARM and resumed
  across at least two carves, one merge feed, one intake turn, and a daemon
  restart;
- a merge changing a file assumed by the next candidate is delivered before
  that candidate and the carver changes/drops/re-decides the proposal;
- no whole-repository rescan occurs on the merge-feed path;
- every admitted handoff traces to a schema-valid proposal, content hash,
  spine revisions, merge cursor, and base revision;
- replay reconstructs session/proposal cursor state;
- compaction retains every required id or fails closed and cold-recovers;
- the independent reviewer remains a distinct session/role and alone gates
  merge readiness;
- human intake reaches the same carver generation under read-only permissions
  and deterministically becomes a follow-up question, D-NNN, backlog item,
  redundant disposition, or typed carve proposal;
- `gated` and `lean` behavior remains unchanged;
- the full serialized `tester-unified` gate is green with changed-line
  coverage; and
- dogfood evidence shows the persistent carver catches a cross-package issue
  the fresh digest-only baseline structurally misses.
