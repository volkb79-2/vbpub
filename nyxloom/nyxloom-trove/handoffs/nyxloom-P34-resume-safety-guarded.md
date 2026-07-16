---
schema_version: 1
id: nyxloom-P34-resume-safety-guarded
project: nyxloom
title: "Resume-safety re-cut: poisoned resumes fresh-start through the dispatch guards"
tier: sonnet5-high
input_revision: "a7499cc"
depends_on: [nyxloom-P32-carve-exit-rescan, nyxloom-P33-robust-review-verdict]
session: fresh
source: {kind: backlog, ref: nyxloom-trove/backlog.md}
scope:
  touch:
    - "src/nyxloom/reconcile.py"
    - "src/nyxloom/config.py"
    - "src/nyxloom/daemon.py"
    - "src/nyxloom/schemas/nyxloom-config.schema.json"
    - "tests/test_reconcile.py"
  forbid:
    - "src/nyxloom/types.py"
    - "src/nyxloom/wrapper.py"
    - "src/nyxloom/storage.py"
    - "src/nyxloom/adapters.py"
oracles:
  - id: O1
    observable: "In `reconcile.plan_project`, an INTERRUPTED attempt that (a) is the LAST element of `tsf.attempts`, (b) belongs to an ACTIVE task, (c) has `resume_failures[attempt_id] >= policy.max_resume_failures`, and (d) still has distinct-record budget, yields a fresh `DispatchImplementer` (no session_handle carried) and NO `ResumeAttempt`. A unit test in tests/test_reconcile.py builds that ReconcileInput and asserts exactly one DispatchImplementer and zero ResumeAttempt for that task."
    negative: "The planner keeps emitting ResumeAttempt for the same poisoned session_handle every pass (today's reconcile.py:446-447), looping into the same broken session forever because resumes reuse one attempt record so attempts_count never trips max_attempts_per_task."
    gate: tester-unified
  - id: O2
    observable: "The healthy path is unchanged: when `resume_failures[attempt_id] < policy.max_resume_failures`, an INTERRUPTED attempt with a session_handle and remaining budget still yields `ResumeAttempt` and no DispatchImplementer. A test asserts ResumeAttempt below the failure threshold."
    negative: "The implementation fresh-restarts on the first interrupt, discarding a resumable session and losing all in-session progress (over-eager fallback)."
    gate: tester-unified
  - id: O3
    observable: "`policy.max_resume_failures` (default 2) and `policy.resume_progress_grace_seconds` (default 120) exist on config.Policy with those defaults; a nyxloom.toml [policy] setting them overrides; omitting them yields the defaults. Both are permitted by src/nyxloom/schemas/nyxloom-config.schema.json, whose policy object is `additionalProperties: false`. Tests: a config-load assertion for defaults plus override, and a schema-validation assertion for a config carrying both."
    negative: "Thresholds are hardcoded constants ignoring nyxloom.toml, OR a nyxloom.toml carrying the new keys fails P24's config-lint because the strict schema was not extended."
    gate: tester-unified
  - id: O4
    observable: "GUARD MATRIX — the fresh-start dispatch is refused in every one of these six states, each asserted by its own test case: (1) the poisoned attempt is not `tsf.attempts[-1]` (a newer attempt exists), (2) the task is AWAITING_REVIEW rather than ACTIVE, (3) `project_paused=True`, (4) `tsf.paused=True`, (5) `budget_remaining=0.0`, (6) the task's mutex lease is held (`leases_free[name]=False`). Each asserts zero DispatchImplementer for that task. Cases 1 and 2 additionally assert the attempt is PARKED — zero actions of any kind for it, so no BLOCKED transition either."
    negative: "The exact defect that got P26 rejected and reverted: the fallback emits DispatchImplementer straight from the attempt-lifecycle block, bypassing every guard, so the daemon launches a real agent per pass into the shared feat/<task> worktree — while paused, at zero budget, without a lease, or alongside a running attempt."
    gate: tester-unified
  - id: O5
    observable: "MULTI-PASS CONVERGENCE — a test plans, applies the resulting fresh DispatchImplementer to the state (append the new attempt record as RUNNING, exactly as daemon.py's handler does), then plans a SECOND pass over the mutated state and asserts zero DispatchImplementer on that second pass. Repeated to exhaustion, the sequence terminates: after `max_attempts_per_task` distinct IMPLEMENTER records the planner emits the typed BLOCKED of O6, never another dispatch."
    negative: "The poisoned record re-triggers on every subsequent pass (P26's unbounded re-dispatch: a new agent process every 30s reconcile interval, each writing into one git worktree)."
    gate: tester-unified
  - id: O6
    observable: "When the distinct-record budget is gone (count of IMPLEMENTER attempt RECORDS >= `policy.max_attempts_per_task`) and the latest attempt is poisoned, the task transitions to BLOCKED with a typed ENVIRONMENT blocker via the existing dead-end path (reconcile.py:456-462), never left silently ACTIVE. A test asserts Transition(to=BLOCKED) with a blocker under that state."
    negative: "A resume-poisoned task with no budget is left ACTIVE forever with no actions (the silent dead-end P14 removed), or is blocked while a healthy newer attempt is still running."
    gate: tester-unified
gates: [tester-unified]
escalate_if:
  - "the decision table below cannot be implemented without editing types.py, wrapper.py, storage.py or adapters.py"
  - "bounding the fresh-start requires a new Action, a new Attempt state, or a new event type"
---

# P34 — Resume-safety re-cut: fresh-start through the dispatch guards

**This is a re-cut of P26, which was merged, rejected on review, and reverted
(`45b0546`).** Its goal stands: a resumed session that keeps dying is currently
resumed **forever**, because resumes reuse one attempt record (`.resume-N`), so
the `attempts_count < max_attempts_per_task` guard (reconcile.py:446) never
trips. The manual operator rule *"DON'T restart the daemon needlessly"* is the
only thing standing in for detection today.

P26's config, schema, and detection work was sound. **Its reconcile decision was
not**, and the way it failed is the whole point of this package — read the next
section before writing any code.

## Why P26 was rejected (read this first)

Full report: `nyxloom-trove/reports/nyxloom-P26-daemon-resume-safety-REVIEW.md`
(on the branch history; recovered with `git show 0b486e5`).

P26 emitted `DispatchImplementer` directly from the attempt-lifecycle block.
Every other dispatch in this codebase passes two gates first —
`dispatch_eligible(fm, tsf, inp)` and the `dispatched >= dispatch_capacity` cap
(reconcile.py:346-356). P26's path passed **neither**, and the daemon's
`DispatchImplementer` handler has no guard of its own: it unconditionally
launches a real agent into the shared `feat/<task_id>` worktree. The reviewer
proved a live dispatch in six states where none was legal, including *while
paused*, *at zero budget*, and *alongside an already-running attempt*.

Worse, it was **unbounded**. `attempts_count` (reconcile.py:444-445) counts only
attempts that are `in TERMINAL_ATTEMPT_STATES and a.receipt`. A poisoned record
is neither: `INTERRUPTED` is not terminal (types.py:111-113), and an attempt
reaching INTERRUPTED via `MarkInterrupted` has no receipt by construction. So
the poisoned record contributed `0` forever while its `.resume-N` logs kept
`resume_failures >= 2` forever — a **new agent process every reconcile pass**,
all into one git worktree. Strictly worse than the bug it fixed.

P26's step 4 was also **not implementable as written**: it said to mark the
poisoned attempt terminal "via the existing MarkInterrupted/Transition idiom",
but the attempt is *already* INTERRUPTED and re-marking is a no-op. The only
legal terminal target is ABANDONED (types.py:125), which needs an action and an
event type that do not exist — both forbidden here. **That contradiction is what
this re-cut resolves: you never mark the poisoned attempt terminal at all.**

## The decision table (the contract)

Bounding comes from two rules that need no new action, state, or event:
**latest-only** (a poisoned record stops mattering once a newer attempt exists)
and a **distinct-record budget** (count attempt RECORDS, not receipts).

For each `attempt` with `state == INTERRUPTED` on a non-terminal task, where
*poisoned* means `resume_failures.get(attempt_id, 0) >= policy.max_resume_failures`:

| Condition | Action |
| --- | --- |
| not poisoned | **unchanged** — today's ResumeAttempt-or-BLOCKED branch (438-462) |
| poisoned, not `tsf.attempts[-1]` | **park** — no action; a newer attempt already supersedes it |
| poisoned, latest, task not ACTIVE | **park** — no action (an AWAITING_REVIEW task has a review in flight) |
| poisoned, latest, ACTIVE, record-budget gone | **typed BLOCKED** — the existing dead-end (456-462) |
| poisoned, latest, ACTIVE, a fresh-start guard refuses | **park** — no action; retry next pass (paused/budget/lease/route are transient) |
| poisoned, latest, ACTIVE, budget + guards clear | **fresh `DispatchImplementer`**, no session_handle |

"Park" means emit nothing for that attempt — like the existing `drain-agents`
branch (429-436). None of the park rows is a silent dead-end: each has either a
newer attempt, a review, or a transient condition that clears.

**Distinct-record budget** = the number of `role == Role.IMPLEMENTER` records in
`tsf.attempts`, compared against `policy.max_attempts_per_task`. Unlike
`attempts_count` it counts receiptless records, so each fresh-start consumes
budget and the sequence terminates (O5). Leave the existing `attempts_count`
computations alone — add this as a separate, clearly-named helper.

## Context to read first (read ONLY these, in order)

- `src/nyxloom/reconcile.py`
  - **438-462** — the INTERRUPTED branch you are re-cutting. 446-447 emits
    `ResumeAttempt`; 456-462 is the typed dead-end you reuse for O6.
  - **340-356** — the lifecycle dispatch loop: `dispatch_eligible` + the
    capacity cap, and the "first healthy route" selection you mirror.
  - **610-670** — `dispatch_eligible` and its eight ordered checks. You reuse
    checks 1 (paused), 6 (budget), 7 (lease) and 8 (healthy route) and must
    **exclude** 4 (wip-cap) and 5 (attempts-exhausted). Both would wrongly
    refuse: the task's own ACTIVE state trips the wip-cap, and the record
    budget of this package replaces the receipt-based attempts check. Factor
    the shared checks rather than duplicating them.
  - **244-272** — `ReconcileInput`. Add `resume_failures: dict[str, int]` with
    `field(default_factory=dict)`, mirroring `log_quiet_seconds`, so existing
    tests that omit it still build.
- `src/nyxloom/config.py` **91-116** — the `Policy` dataclass; add the two keys
  next to `stall_log_quiet_seconds`, and confirm the toml loader reads them.
- `src/nyxloom/schemas/nyxloom-config.schema.json` — the `policy` object is
  `additionalProperties: false`, so both keys must be added or a config using
  them fails config-lint (O3's negative).
- `src/nyxloom/daemon.py`
  - **715-760** — `_attempt_scan`, the input-building idiom (`log_quiet_seconds`
    / `pid_alive` / `receipts` from attempt dirs) your detection mirrors.
  - **1330-1332** — `_next_resume_n`: resume logs are `attempt.resume-{n}.log`
    in `paths.attempt_dir(project, attempt_id)`. This naming is the contract
    your scan reads.
  - **1380-1412** — the `DispatchImplementer` handler. Note it ends with
    `_transition(..., TaskState.ACTIVE)`; ACTIVE -> ACTIVE is a silent no-op
    (storage.py's from==to idempotency), which is why restricting fresh-start
    to ACTIVE tasks is safe. Do NOT change this handler.
- `tests/test_reconcile.py` **29-56** — `make_config`; extend it with the two
  new policy kwargs. The helpers `make_attempt` / `make_tsf` / `make_routes`
  build the O1/O2/O4/O5/O6 fixtures.

## Work

1. `config.Policy`: add `max_resume_failures: int = 2` and
   `resume_progress_grace_seconds: int = 120`; wire both through the toml loader.
2. `schemas/nyxloom-config.schema.json`: permit both new integer `[policy]`
   keys; keep the object strict otherwise.
3. `reconcile.ReconcileInput`: add `resume_failures: dict[str, int]`
   (default-factory dict).
4. `reconcile.py`: add a fresh-start eligibility helper covering paused /
   budget / lease / healthy-route, excluding wip-cap and attempts-exhausted,
   sharing code with `dispatch_eligible` rather than copying it. Add a
   distinct-record budget helper (IMPLEMENTER records vs `max_attempts_per_task`).
5. `reconcile.plan_project`, INTERRUPTED branch: implement the decision table
   exactly. Reuse the existing "first healthy route" selection for the fresh
   `DispatchImplementer`; carry no session_handle.
6. `daemon.run_pass`: compute `resume_failures` into the ReconcileInput —
   for an attempt that is INTERRUPTED with no receipt, count the
   `attempt.resume-{n}.log` files in its attempt dir whose mtime is older than
   `policy.resume_progress_grace_seconds`. Pure input-building; add no Action
   handler. **Do not score progress by log size.** A resume that worked leaves
   the attempt RUNNING or EXITED-with-receipt, so an attempt sitting INTERRUPTED
   with N aged resume logs has had N failed resumes by construction. P26 used a
   `size <= 200 bytes` rule that scored a noisily-dying session (stack traces,
   retry spam) as progress — the original bug, undetected. The grace window is
   only a race guard, so a just-launched resume whose ATTEMPT_RESUMED has not
   landed is not miscounted.
7. Tests in `tests/test_reconcile.py` for O1, O2, O4 (six cases), O5, O6, plus
   the config/schema assertions for O3.

## Scope / forbid

Touch ONLY the five files in `scope.touch`. `types.py`, `wrapper.py`,
`storage.py` and `adapters.py` are forbidden — no new Attempt state, no new
Action, no new event type, no receipt-semantics change. If the decision table
genuinely cannot be met within those files, that is a BLOCKED trigger, not a
workaround. P26 was rejected for improvising past exactly this boundary.

## BLOCKED rule

If a named contract cannot be met as specified, or scope requires a forbidden
file (see `escalate_if`), STOP — write `BLOCKED: <reason>` to the LOG, commit,
and exit. Do NOT improvise a workaround. This is a success mode (the package
re-routes), not a failure.

## Worktree / branch

The daemon runs this on a dedicated `implement` branch in its own git worktree
under `.worktrees/` (branch `feat/nyxloom-P34-resume-safety-guarded` from
`main`); commit all work on that branch. Do not touch the main checkout.

## Gate

`tester-unified` (the project's real gate — never the cockpit):

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd {worktree}/nyxloom && PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```
