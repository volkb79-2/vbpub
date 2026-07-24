# Design: agent correctness, the controller, and where intelligence must live

Status: canonical design guide · authored 2026-07-24 (from a live human-driven build of
the findings-channel epic, FN-1..6, on this repo). Governs how nyxloom pushes correctness
into MECHANISM so agents produce correct code without a smart controller in the loop.

**North-star of this doc:** push correctness into mechanism wherever the property is
*checkable*, and reserve the model for the few genuinely-fuzzy, *typed* judgments (carve,
review, triage). The less judgment the orchestration needs, the closer nyxloom gets to
running unattended.

---

## 1. What the coverage gate actually is (and its one blind spot)

`coverage_gate.py:evaluate` is a pure set-intersection, zero semantics:

1. `git diff --unified=0` vs the **merge-base** → the set of NEW-side line numbers the
   change added/touched, per file (`parse_added_lines`).
2. `coverage.py` runs the suite with the interpreter's line tracer on → per file,
   `executed_lines` and `missing_lines`. A changed line in *neither* set is
   non-executable (comment/blank) and is ignored — we never re-derive "is this code?".
3. Verdict: `uncovered = (changed ∩ (executed ∪ missing)) ∩ missing`. Any uncovered
   changed line → fail (100% floor).

It proves **exactly one thing: every executable line you added was *executed* by ≥1
test.** It is structurally blind to whether the line's *behavior was asserted*. This is
why, across the FN-1..6 epic, every coverage miss was a real reject-path/edge-branch the
tests *ran through but never checked* — and why a **hollow** test (runs the line, asserts
nothing meaningful) passes the gate. The gate measures **reach**, not **correctness**.

## 2. The correctness ladder — pushing proof from judgment into mechanism

Each rung mechanizes a check the reviewer/controller otherwise does by judgment. **nyxloom
adopts all of these (operator decision 2026-07-24).**

| Property to prove | Mechanism | Status |
|---|---|---|
| the line is **reached** | diff-coverage gate (100% floor, merge-base) | LIVE (`coverage_gate.py`) |
| the line's **behavior is asserted** (kills hollow tests) | **changed-lines mutation gate** — mutate the diff's code, re-run tests; a surviving mutant = a hollow test | **BUILDING** (this doc's flagship change) |
| the **contract** is real | **negative-oracle enforcement** — every handoff oracle must carry a real `negative:` assertion | **ALREADY LIVE** — lint rule **L3** rejects a `negative` that is empty, `n/a`, or a copy of `observable`. (Possible strengthening: assert the negative is *test-backed*, not just present.) |
| cross-cutting **invariants** hold | `test_invariants.py` — every new EventType is projection-classified, transitions well-formed, etc. Grow this set. | LIVE + ongoing doctrine |
| **type** correctness | strict typing on the changed surface | ongoing doctrine |
| **state** correctness | the deterministic reconcile state machine (§5) | LIVE (`reconcile.plan_project`) |

**The load-bearing insight:** the gate is a *reach* proof; **mutation testing is a
*behavior* proof** and it mechanizes exactly what a human reviewer does ("would this test
still pass if the impl were wrong?"). Stacking reach + behavior + contract proofs shrinks
the reviewer's remaining job to genuinely-fuzzy design/security judgment — which is small
and cheap. Mutation testing is scoped to **changed lines only** (pairs with the diff-cov
machinery) so it's affordable.

## 3. The three-layer model, and why each layer earns its place

Empirically, across 6 packages built by a cheap implementer (deepseek-flash) + a resumed
pro reviewer (deepseek-v4-pro@max) + a human controller:

- **GATE = the correctness FLOOR** (deterministic). Caught coverage misses the reviewer
  counted as "tested" **4 times** (FN-3/4/6/5). Never wrong, never negotiable.
- **REVIEWER = a cheap design/security layer** (~$0.002/pkg, cache-hit climbing 95.8%→
  99.7% on a resumed session). Caught what the gate structurally can't — a `<script>`
  placed outside `<body>` (tests pass because the script is *present*); verified an entire
  daemon-endpoint security surface. Probabilistic: it can false-positive and false-negative.
- **CONTROLLER = spec authoring + harness correctness.** Caught what neither layer could —
  a two-dot-diff false-blocker (the reviewer had no merge-history; fixed by using the
  three-dot/merge-base diff, matching the gate). The controller's *irreplaceable* residual
  is **carving** (why the cheap implementer one-shots packages) — NOT per-package
  re-reading.

**Workflow doctrine:** review feedback flows **reviewer → implementer** (forward the
reviewer's OWN findings verbatim to a resumed implementer; the controller does not
hand-author critiques). Mechanical **gate** output (coverage %, pass/fail) is objective and
relayed as-is. The controller validates the reviewer (rejects false-positives) until the
reviewer's inputs are provably consistent (three-dot everywhere — DONE).

**Is the dispatch+evaluate loop cheap-model-able?** Yes — and nyxloom already proves it:
`nyxloomd` is pure code (§5), scope enforcement and receipt-scanning are mechanical. The
expensive residual is not "evaluate the agents", it's **carve the work**.

## 4. Reviewer harness requirements (hard-won)

- **THREE-dot / merge-base diff, always** (`git diff main...branch`). A two-dot diff shows a
  sibling's already-merged work as deletions → confident false-blockers that, if executed,
  delete real code. Match the coverage gate's own scoping.
- **Resumed session** (`reasonix run -c -model deepseek-pro-max -dir <stable>`) for the
  cache economics; the rubric + accumulated codebase context stay provider-cached.
- **Typed verdict** (`VERDICT: CLEAN|CHANGES_REQUESTED` + findings), so the controller can
  route it mechanically.

## 5. The pipeline review (`carve → implement → self_review → frontier_review → triage → auto_merge → post_merge_gate`)

Ground truth from `stages.py` + `daemon.py`:

### 5a. Where the DETERMINISTIC gate runs — the actionable finding
The stage roles: `carve`/`implement`/`self_review`/`frontier_review` are **agent** stages;
`triage`/`auto_merge`/`post_merge_gate` are **`role=None` (mechanical)**. There is **no
deterministic `gate` stage before `auto_merge`.** `daemon.py:3728` calls the frontier
reviewer "the real gate" — i.e. the PRE-merge bar is an **AI** (told to run the gate via
`gate_hint`, but an AI trusting/running a command), and the authoritative DETERMINISTIC
gate is `post_merge_gate`, which re-runs the implementation gate **AFTER** the merge to main
(`daemon.py:1910`, MERGED→VALIDATING→COMPLETED|BLOCKED).

**Assessment.** `post_merge_gate` as a **second, integration** gate is GOOD — it catches
cross-package interactions an isolated worktree gate can't (exactly the "final main-suite
validation" step). But relying on the AI `frontier_review` as the ONLY *deterministic*
pre-merge check is the **false-green / main-goes-red** risk: an AI false-approve merges
broken code, `post_merge_gate` then fails, and main is already red (task → BLOCKED). The
human build deliberately gated **SOLO (deterministic) BEFORE merge** so main never goes red.

**Recommendation (D-CORRECT-1):** add a deterministic **`gate` stage** (`role=None`) between
`frontier_review` and `auto_merge` — runs the handoff's `gates:` command, pass →
MERGE_READY-equivalent, fail → REVIEW_REJECTED/QUEUED. Keep `post_merge_gate` as the
integration re-check. Net: deterministic gate authoritative *pre*-merge (main never red) +
integration gate *post*-merge. The AI `frontier_review` becomes design/security judgment,
not the correctness gate.

### 5b. `frontier_review` → rename `review_independent`
The stage NAME implies a frontier/expensive model, but the ROLE is an **independent** review
(independent of the implementer) — and it's run by deepseek-v4-pro, not a frontier model.
Rename the stage/role to **`review_independent`** (or `independent_review`) so the stage
name is decoupled from the model tier (the tier is a routing decision in the capability
matrix — carve-N/review-N/implement-N — not baked into the stage name). Cosmetic but
clarifying; touches `stages.py` Role enum + the routing matrix + tests. (D-CORRECT-2.)

### 5c. `triage` — mechanical stage, LLM-produced class
The `triage` **stage is mechanical** (`role=None`): its declared floor is
`fixable→QUEUED`, `exhausted→NEEDS_DECISION`, with B4a/B4b context-sensitive upgrades
(`architectural/stale-premise→READY_TO_CARVE` when a carve stage exists) applied **in
reconcile**. The **classification** (`ReconcileInput.triage_class: dict[task_id→class]`, the
B4b "LLM triage tier") is produced by an LLM and **consumed mechanically** — the canonical
nyxloom pattern: *an LLM emits a typed judgment at a fuzzy boundary; the state machine stays
crisp.* **A long-running CHEAP model (deepseek-flash) is a good fit for producing
`triage_class`** — the output is a small typed enum consumed deterministically, so a cheap
model is safe here, and a long-lived session would accumulate the project's rejection
patterns. (D-CORRECT-3.)

### 5d. State changes are mechanical — keep them that way
`reconcile.plan_project(ReconcileInput) → PlanResult` is a deterministic state machine over
`TASK_TRANSITIONS`, driven by gate/verdict return values — auditable, free, unit-tested,
correct-by-construction. **Do NOT replace it with an LLM** ("what to run next"): that injects
nondeterminism, cost, and unauditability into something already correct. The LLM belongs at
the fuzzy boundaries (carve, review, triage-class), never the crisp transitions. A
long-running cheap agent CAN help the *other* fuzzy call — "given roadmap + what just merged
+ what's queued, what's the highest-value carve next?" — but **advisory + typed** (it
proposes; the machine enforces mutex/caps/transitions). See the carver plan (§6).

## 6. The long-running carver (the context-holder)

The FN-1..6 build's clearest lesson: **only the context-HOLDING controller caught
cross-package issues a fresh-per-task agent structurally cannot.** Today the carver reads a
`SPINE-DIGEST.md` **fresh** each carve (B6) — a snapshot, not accumulated context.

**Direction (operator decision 2026-07-24):** a single **long-running, resumed carver
session per project** that (a) holds north-star/roadmap/spine/backlog persistently, (b) is
fed a typed **merge-digest** after each handoff merges (so its model of "what exists now"
stays current without a repo re-scan), (c) does **NOT** review (the independent reviewer is
separate), (d) is **externally compacted** by the daemon (retention-steered: KEEP spine +
open decisions + recent merges; DROP resolved carves), and (e) is the **human-intake
surface** — new user plans/raw feature ideas go here to be fitted into current work-state.

Full design: **`docs/plan-long-running-carver.md`** (authored by sol@high — 6 gated
packages P1..P6). Key architectural decision: **"long-running" = a resumed PROVIDER session
across many bounded daemon-launched turns, NOT a resident process** — this preserves the
crash-safe detached-wrapper model (the wrapper acquires the `<project>.strategic-carver`
lease per turn; the durable session id / generation / event-cursor live in event-sourced
state). Every merge emits a bounded **typed merge-digest** (no repo re-scan); every carver
result is a **schema-checked proposal** that pure `plan_project` admits mechanically (§5d).
Model tier: **carve-3 on sol@high** (carving is the highest-judgment role; review is
capability-MATCHED to the implementation, not auto-max — a deliberate rejection of
inverted-effort). Disabled for carve-less `gated`/`lean` presets.

**External compaction mechanism** — `docs/research-external-compaction.md` (findings): the
only tool with a *documented external-compaction trigger* is **codex via app-server
(`thread/compact/start` + wait for `contextCompaction`, with a `compact_prompt` retention
template)** — the recommended production path. reasonix (v1.17.12) has no mid-turn force;
use bounded `run -c/--resume` cycles + tune `compact_ratio`/`compact_force_ratio`/
`cold_resume_prune` (its automatic policy). Claude Code: Agent SDK resume + `/compact` +
`max_turns=1`. **Single-owner discipline** (one controller per session; record
session/compaction ids + pre/post token telemetry + the spine revision used for retention).
**Durable-ground-truth mitigation:** the spine files are the always-re-readable truth, so
the session holds only WORKING context and a lossy compaction is recoverable — the daemon
writes/validates spine revision N *before* compacting and the carver re-reads it *after*.

---

## Decisions opened by this doc
- **D-CORRECT-1** — deterministic pre-merge gate before publish (main never red).
  ✅ **DONE** (merged `a8772860`): `_execute_auto_merge` gates the merged scratch tree before
  `update-ref`; fail → REVIEW_REJECTED; `policy.pre_merge_gate` (default on).
- **D-CORRECT-2** — rename `frontier_review` → `review_independent` (decouple name from tier).
  ✅ **DONE** (merged `a2c9e50e`): all three string forms renamed (`Role.FRONTIER_REVIEW` →
  `REVIEW_INDEPENDENT`, value `"frontier-review"` → `"review-independent"`, stage key
  `frontier_review` → `review_independent`) with narrow read-compat at 3 persistence boundaries
  (`Role._missing_`, `Routes.for_role` legacy-tier fallback, statefile schema enum accepts both).
  The four `*_AGENT_TIER` tier constants (decision/intake/assessment/questionnaire) are a SEPARATE
  tier migration and were intentionally left `"frontier-review"` + guard-tested — a naive rename of
  those would pass tests yet silently break routing against the live config. pro-max review caught a
  missed top-level `schemas/statefile.schema.json` duplicate.
- **D-CORRECT-3** — `triage_class` via a long-running cheap model (typed, safe).
  ⏳ **folds into F018** (same long-running-cheap-agent-emits-typed-judgment pattern as the carver).
- **D-CORRECT-4** — changed-lines mutation gate (behavior proof).
  ✅ **DONE** (merged `0ea18888`): `mutation_gate.py`. NOTE: it is a standalone tool; wiring it
  into the pipeline as an actual gate is the remaining half of F017.
- **D-CORRECT-5** — negative-oracle enforcement. ✅ **ALREADY LIVE** = lint rule L3.
- **D-CORRECT-6 / F018** — long-running, harness-portable, externally-compacted carver.
  📋 **PLANNED** (`docs/plan-long-running-carver.md`, sol@high, 6 packages P1–P6). Refinement
  from the compaction discussion: durable state = spine + a HARNESS-NEUTRAL working summary, so
  runner+model are a per-turn choice (cheap reasonix by default, escalate a hard carve to
  sol/Claude by re-seeding from spine+summary); compaction is per-runner (Claude `/compact`,
  codex app-server `thread/compact/start`, reasonix/opencode tuned auto-compaction).
