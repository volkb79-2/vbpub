# nyxloom-P26-daemon-resume-safety — FRONTIER REVIEW

- **Reviewer:** independent frontier reviewer (merge gate)
- **Date:** 2026-07-16
- **Branch:** `feat/nyxloom-P26-daemon-resume-safety` @ `9f20d5c`
- **Verdict:** ❌ **REJECTED** — architectural defect in the core decision path

## Gate

Re-run by the reviewer, not trusted from the report:

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local \
  bash -c 'cd .../feat/nyxloom-P26-daemon-resume-safety/nyxloom && \
           PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -q'
```

**459 passed.** The gate is genuinely green and the branch's own tests are honest
about what they assert. The gate passing is not the issue — what the tests
*don't* cover is.

## Git state (verified, not from the receipt)

- `git log main..feat/...` → exactly one commit, `9f20d5c`.
- `git status` in the branch worktree → clean; no uncommitted work.
- Files touched match `scope.touch` exactly; no forbidden file
  (`wrapper.py` / `adapters.py` / `storage.py` / `types.py`) was modified.

## What is correct

- **O3 is fully met.** `policy.max_resume_failures = 2` and
  `policy.resume_progress_grace_seconds = 120` exist with the specified
  defaults, override from `[policy]`, and both keys are permitted by
  `nyxloom-config.schema.json`. Tests cover defaults, override, and schema
  acceptance.
- **O2 is met.** Below-threshold interrupts still yield `ResumeAttempt`; the
  healthy path is genuinely unchanged.
- **The detection wiring is sound.** `daemon._resume_failures_scan` builds
  `attempt.resume-{n}.log` (daemon.py:797) with the same convention the resume
  handler writes (daemon.py:1482) — the names really do match, so the input is
  not dead code. Extracting `_first_healthy_route` is a clean, correct
  refactor.

## Rejection: the fresh-start dispatch bypasses every dispatch guard in the system

`reconcile.plan_project` emits `DispatchImplementer` directly from the
**attempt-lifecycle block** (reconcile.py:459-462). Every other dispatch in
this codebase goes through the lifecycle block's two gates —
`dispatch_eligible(fm, tsf, inp)` and the `dispatched >= dispatch_capacity`
cap (reconcile.py:347-358). The P26 path goes through **neither**, and the
daemon's `DispatchImplementer` handler (daemon.py:1424-1465) has no guard of
its own: it unconditionally `wrapper.launch_detached(spec)`s a real agent
process into the shared `feat/<task_id>` worktree.

I proved each of the following against the branch with scratch tests
(removed before commit; repro at the bottom). Every one of them **currently
dispatches a real implementer**:

| # | Scenario | Expected | Actual |
|---|---|---|---|
| 1 | Pass 2: poisoned `att-1` still INTERRUPTED, fresh `att-2` RUNNING | no dispatch | **1 dispatch** |
| 2 | Task in `AWAITING_REVIEW` (a later attempt already succeeded) | no dispatch | **1 dispatch** |
| 3 | `project_paused=True` | no dispatch (`'paused'`) | **1 dispatch** |
| 4 | `tsf.paused=True` | no dispatch (`'paused'`) | **1 dispatch** |
| 5 | `budget_remaining=0.0` | no dispatch (`'budget-exhausted'`) | **1 dispatch** |
| 6 | Mutex lease held by another task | no dispatch (`'lease-unavailable'`) | **1 dispatch** |

### Why #1 is unbounded, not a one-off

`attempts_count` (reconcile.py:446-447) counts attempts that are
`in TERMINAL_ATTEMPT_STATES and a.receipt and result != LIMIT`. The poisoned
attempt satisfies **none** of that: `TERMINAL_ATTEMPT_STATES` is
`{EXITED, FAILED, ABANDONED}` — INTERRUPTED is *not* terminal — and an attempt
reaching INTERRUPTED via `MarkInterrupted` has **no receipt** by construction
(that branch's precondition is "no receipt, pid dead", reconcile.py:401).

So the poisoned record contributes `0` to `attempts_count` forever, while its
`.resume-N` logs stay on disk forever, so `resume_failures[att-1] >= 2`
forever. `poisoned and has_budget` is therefore **permanently true**, and the
planner re-emits `DispatchImplementer` on *every pass*.

This does not fix O1's negative — it re-implements it in a more expensive form.
The old bug resumed one poisoned session serially. The new behaviour spawns a
**new agent process every reconcile pass** (30s default), each into the *same*
`feat/<task_id>` worktree, i.e. multiple concurrent writers on one git
worktree. That is strictly worse than the bug being fixed.

### Why #2 is the severe one — and why the feature causes it

This is not a hypothetical: P26's own fresh-start *creates* the state that
triggers it. Pass 1 fresh-starts `att-2`; `att-2` succeeds; task goes
`AWAITING_REVIEW`; `att-1` is still INTERRUPTED and still poisoned. The
INTERRUPTED branch's only task-state guard is `!= BLOCKED` (reconcile.py:440)
and `AWAITING_REVIEW` is not in `TERMINAL_TASK_STATES`
(`{COMPLETED, SUPERSEDED, CANCELLED}`), so the branch runs and dispatches.

The daemon then executes it in this order: `launch_detached()` spawns the agent
(daemon.py:1462) **and then** `self._transition(..., TaskState.ACTIVE)`
(daemon.py:1465) raises — `AWAITING_REVIEW → ACTIVE` is not in
`TASK_TRANSITIONS`. Net effect per pass: an **orphaned agent process** writing
into the worktree a reviewer is concurrently reading, plus TICK_ERROR spam, and
no state record of the attempt. Repeating every 30s.

## Why this is REJECT rather than reviewer-fixed

Per the role contract I fix small defects myself. This is not small, and the
in-scope fixes are all blocked or are design decisions that are not mine to
improvise:

1. **Stopping the re-trigger** requires making the poisoned attempt stop being
   reconsidered. The legal target from INTERRUPTED is `ABANDONED`
   (`ATTEMPT_TRANSITIONS`, types.py:126) — but **no `AbandonAttempt` action
   exists**, and the handoff explicitly forbids adding one ("Do NOT add or
   change any Action handler in the EXECUTION MAP"). `EmitAttemptExit` cannot
   substitute: `INTERRUPTED → EXITED` is not a legal transition. The handoff's
   own step 4 ("mark the poisoned attempt terminal ... via the existing
   MarkInterrupted/Transition idiom") is **not implementable as written** — the
   attempt is *already* INTERRUPTED, and re-marking it is a no-op.
2. **Reusing `dispatch_eligible` wholesale is wrong** and would deadlock: its
   wip-cap check counts the poisoned task's own ACTIVE state, so with
   `max_active_tasks=1` the task could never fresh-start. A correct fix must
   cherry-pick which gates apply (paused / budget / lease / route yes; wip-cap
   no) — a real design decision.

This is precisely what `escalate_if` and the BLOCKED rule existed for. The
correct move was to STOP and emit `BLOCKED: cannot mark a poisoned attempt
terminal without a new Action handler or a types.py change` — a documented
success mode that re-routes to the controller. Instead the contract was met
literally (each oracle's single-pass assertion passes) while its stated purpose
— "stop resuming a poisoned session and don't loop" — was not.

## Oracle assessment

| Oracle | Test passes | Contract actually met |
|---|---|---|
| O1 | ✅ | ❌ negative ("unbounded loop") still true, in a worse form |
| O2 | ✅ | ✅ |
| O3 | ✅ | ✅ |
| O4 | ✅ | ⚠️ only when a prior attempt carries a receipt; the common poisoned case never reaches BLOCKED because `attempts_count` never counts the poisoned record |

The O1/O2/O4 tests each construct a task with a **single attempt** and run a
**single pass** — the one configuration in which all six defects are invisible.
The suite is not dishonest, but it is load-bearing in the wrong place.

## Additional findings (non-blocking, for the re-cut)

- **`_resume_failures_scan` has zero test coverage.** It is the sole producer
  of `resume_failures`; nothing in the diff exercises it. The reconcile tests
  inject the dict directly, so a regression here would be silent.
- **Detection heuristic is narrow (matches the handoff, so not a defect).**
  `size <= 200 bytes AND age >= grace` only catches resumes that die nearly
  silently. A poisoned session that dies *noisily* (stack traces, retry spam)
  exceeds 200 bytes and is scored as progress — so it resumes forever, which is
  the original bug, undetected. Worth revisiting in the re-cut handoff.

## Repro

Drop in `tests/`, run with `PYTHONPATH=src:tests`:

```python
from nyxloom.reconcile import DispatchImplementer, ReconcileInput, plan_project
from nyxloom.types import AttemptState, TaskState
from test_reconcile import (make_attempt, make_config, make_frontmatter,
                            make_routes, make_tsf, utc)

def test_second_pass_redispatches():
    cfg = make_config(max_resume_failures=2, max_attempts_per_task=3)
    fm = make_frontmatter(id="P01", tier="flash-high")
    poisoned = make_attempt(attempt_id="att-1", state=AttemptState.INTERRUPTED, receipt=None)
    poisoned.session_handle = "sess-poisoned"
    fresh = make_attempt(attempt_id="att-2", state=AttemptState.RUNNING, receipt=None)
    tsf = make_tsf(task_id="P01", state=TaskState.ACTIVE, attempts=[poisoned, fresh])
    inp = ReconcileInput(
        now=utc(2026, 7, 15), cfg=cfg, routes=make_routes(tier="flash-high"),
        states={"P01": tsf}, frontmatters={"P01": (fm, "h.md")}, lint_clean={},
        project_paused=False, decisions_open=set(), merged_branches=set(),
        leases_free={}, provider_ok={"route-1": True}, log_quiet_seconds={},
        pid_alive={"att-2": True}, receipts={}, resume_failures={"att-1": 2},
    )
    # FAILS on 9f20d5c: 1 dispatch — a second agent into the same worktree.
    assert [a for a in plan_project(inp) if isinstance(a, DispatchImplementer)] == []
```

Swap the last two lines for `project_paused=True` / `budget_remaining=0.0` /
`tsf.paused=True` / `leases_free={"demo-global": False}` /
`state=TaskState.AWAITING_REVIEW` to reproduce defects 2-6.

## Recommendation for the re-cut handoff

The package is a good idea with sound config/schema/detection work — O2 and O3
can be kept as-is. The reconcile decision needs a re-cut that resolves the
contradiction the original handoff did not: **a poisoned attempt must become
terminal, and there is no in-scope way to do that today.** Pick one and say so
explicitly in `scope.touch`:

1. Allow a new `AbandonAttempt` action + handler (INTERRUPTED → ABANDONED is
   already legal), then gate `attempts_count` on it; **or**
2. Restrict the fresh-start to the *latest* attempt only
   (`attempt is tsf.attempts[-1]`) so a lingering record cannot re-trigger; and
3. Either way, route the fresh dispatch through the paused / budget / lease /
   healthy-route gates (explicitly *excluding* wip-cap, which the task's own
   ACTIVE state would otherwise trip), and require a **multi-pass** test:
   plan → apply → plan again → assert no second dispatch.
