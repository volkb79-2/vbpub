# P45 — REPORT (Close the READY_TO_CARVE dead-end)

Branch: `feat/nyxloom-P45-ready-to-carve-triage`
Worktree: `/workspaces/vbpub/nyxloom/.worktrees/nyxloom-P45-ready-to-carve-triage/nyxloom`
Input revision: `f38d306` (post-P44 main)
Commit (this package): `bd8c4c6` (see `git log -1` on this branch after commit)

## Gate (tester-unified, the ONLY gate that counts)

```
docker run --rm -v /home/vb/volkb79-2/vbpub:/workspaces/vbpub tester-unified:local bash -c \
  'cd /workspaces/vbpub/nyxloom/.worktrees/nyxloom-P45-ready-to-carve-triage/nyxloom && \
   PYTHONPATH=src /opt/tester-venv/bin/python -m pytest tests -p no:cacheprovider 2>&1 | tail -150'
```

Result:

```
........................................................................ [  9%]
........................................................................ [ 18%]
........................................................................ [ 27%]
........................................................................ [ 37%]
........................................................................ [ 46%]
.....................x.................................................. [ 55%]
........................................................................ [ 65%]
........................................................................ [ 74%]
........................................................................ [ 83%]
........................................................................ [ 93%]
....................................................                     [100%]
771 passed, 1 xfailed in 233.82s (0:03:53)
```

**771 passed, 1 xfailed, 0 failed.** Re-ran with `-rx` to confirm the one
remaining xfail is `tests/test_invariants.py::test_no_dead_end_draft` (the
pre-existing, unrelated, out-of-scope `TaskState.DRAFT` gap) -- NOT
`test_no_dead_end_ready_to_carve`, confirming this package's own target
xfail was genuinely removed and now passes for real, not left in place by
accident.

### Reconciling the exact numbers against the stated baseline (765 passed, 2 xfailed)

- `test_no_dead_end_ready_to_carve`'s `xfail(strict=True)` marker removed;
  it now passes for real: **-1 xfailed, +1 passed** (recategorized, same
  test slot).
- 5 new tests added to `tests/test_reconcile.py` (O2/O3/O4 non-hollow
  anchors), all passing: **+5 passed**.
- `test_review_rejected_attempts_exhausted_documents_known_gap` was
  repurposed in place (renamed
  `test_review_rejected_attempts_exhausted_routes_to_ready_to_carve`, same
  slot, O1(a) anchor) -- net zero on counts.
- `test_review_rejected_with_budget_remaining_requeues` (O1(b)'s regression
  pin) left byte-for-byte unchanged -- net zero.

765 (baseline passed) + 1 (xfail->pass) + 5 (new tests) = **771 passed**.
2 (baseline xfailed) - 1 (removed) = **1 xfailed**. Matches the observed
gate output exactly. 0 failed.

## What changed in `reconcile.py`

### REJECT LOOP (module contract item 10)

Extended the existing `if tsf.state == TaskState.REVIEW_REJECTED:` block in
`plan_project`'s task-lifecycle loop. The attempts-remaining branch
(`Transition(to=QUEUED)`) is **completely unchanged**. Added an `else`
branch for the exhausted-budget case (previously a documented, deliberate
no-op -- the "KNOWN GAP"): now plans
`Transition(task_id=fm_id, to=TaskState.READY_TO_CARVE, notes="review
rejected -- attempt budget exhausted; routed for re-carve")`. This edge is
already legal in types.py's frozen `TASK_TRANSITIONS[REVIEW_REJECTED]`; no
types.py edit.

### READY_TO_CARVE handler (new module contract item 12)

New block in `plan_project`, right after the per-task lifecycle loop and
before the QUEUED-dispatch section (item 3). It:

1. Computes `carve_in_flight` (any non-terminal task carrying an Attempt
   with `role=CARVER`) and `frontier_route_available` (a healthy
   `frontier-review`-tier route in `inp.provider_ok`) **once**, reusing the
   *exact same expressions* item 9's own untargeted headroom-refill trigger
   used before this package (that code was moved up and de-duplicated, not
   copied).
2. If neither guard blocks, and one or more tasks are currently
   `READY_TO_CARVE` (from `inp.states`), picks the single lowest
   (sorted) `task_id` and appends, into that task's own action bucket:
   - `CarveDispatch(project=inp.cfg.project_id, task_id=chosen_id)` --
     `item_id` left `None` (the untargeted carve-packet path;
     `daemon._execute_carve_dispatch`/`_build_carve_packet` already embed
     "recent REVIEW_RECORDED follow-ups" for free on that path -- the
     rejection's own context, no new plumbing needed).
   - `Transition(task_id=chosen_id, to=TaskState.SUPERSEDED, notes=...)` in
     the SAME pass -- self-limiting, so a later pass never re-fires a
     second `CarveDispatch` for the same task once the carve slot frees.

No new `Action` subclass, no new `Daemon` method: the handler dispatches
through the exact same `reconcile.CarveDispatch` / `daemon.
_execute_carve_dispatch` path item 9 and P41's `dispatch_targeted_carve`
already share.

### O3 — the single-carve-in-flight-per-pass cap

A module-level `carve_dispatch_planned` flag (initialized `False` before
the READY_TO_CARVE handler runs) is set `True` the moment either trigger
plans a `CarveDispatch` in this pass. Item 9's own block (further down,
past dispatch/attempts/waves/spec) was changed from

```python
if not carve_in_flight:
    ...
```

to

```python
if not carve_in_flight and not carve_dispatch_planned:
    ...
```

and no longer recomputes `carve_in_flight` / `frontier_route_available`
itself -- it reads the same variables the READY_TO_CARVE handler already
computed. Because the READY_TO_CARVE handler runs earlier in the function
body, it wins ties within a pass (if both would independently want to
fire), and item 9 is then structurally prevented from firing a second one.
Within the READY_TO_CARVE handler itself, only the single lowest-sorted
`task_id` is ever chosen even when multiple tasks qualify -- so the
combined guarantee is: **at most one `CarveDispatch` total, across BOTH
triggers, in a single `plan_project` call.** This is the concrete
mechanism for "the single strategic carver remains the sole carve
authority."

### CarveDispatch dataclass docstring

Updated: `task_id` is no longer described as "always None" -- item 12 now
sets it to the triggering task's own id for log/test attributability. Its
docstring is explicit that `daemon._execute_carve_dispatch` never reads
`task_id` (verified by reading L1656-1741: only `action.item_id` and the
separately-passed `project`/`cfg`/`states` params are used; the carve
always mints its own fresh synthetic `carve-{project}-{seq}` task) -- so
this is informational only, never a second execution path.

## Non-hollow anchors per oracle

- **O1(a)** — `tests/test_reconcile.py::
  test_review_rejected_attempts_exhausted_routes_to_ready_to_carve`
  (repurposed from `..._documents_known_gap`): `max_attempts_per_task=1`,
  one EXITED/DONE attempt already recorded -> asserts the single planned
  `Transition` for that task has `to == TaskState.READY_TO_CARVE`,
  `blocker is None`, and notes mentioning both "review rejected" and
  "exhausted".
- **O1(b)** — `tests/test_reconcile.py::
  test_review_rejected_with_budget_remaining_requeues` (byte-for-byte
  UNCHANGED): attempts remaining -> still asserts `Transition(to=QUEUED)`.
- **O2** — `tests/test_invariants.py::test_no_dead_end_ready_to_carve`
  (xfail marker removed, now passes for real) proves SOME action touches a
  bare READY_TO_CARVE task under a favorable input.
  `tests/test_reconcile.py::
  test_ready_to_carve_dispatches_existing_carve_mechanism_then_supersedes`
  strengthens this: asserts the action is specifically a `CarveDispatch`
  with `task_id == "P01"`, `item_id is None`, `project == "demo"` -- and
  that no second Action subclass or Daemon method was invented (checked by
  code review, not just test assertion: `git diff` shows no new class
  definitions).
- **O3(a)** — `tests/test_reconcile.py::
  test_ready_to_carve_no_dispatch_when_carver_already_inflight`: a CARVER
  attempt already RUNNING on an unrelated ACTIVE task -> zero
  `CarveDispatch` planned; the READY_TO_CARVE task gets no `Transition`
  either (stays parked for a later pass).
- **O3(b)** — `tests/test_reconcile.py::
  test_ready_to_carve_two_simultaneous_only_one_carve_dispatch_total`: TWO
  tasks (`P01`, `P02`) simultaneously READY_TO_CARVE (an input that ALSO
  satisfies item 9's own untargeted-trigger conditions, proving the cap is
  truly shared, not merely enforced within one handler) -> exactly ONE
  `CarveDispatch` total across the whole action list, attributed to the
  lower sorted id (`P01`); `P02` gets nothing this pass.
- **O4** — the same
  `test_ready_to_carve_dispatches_existing_carve_mechanism_then_supersedes`
  test also asserts a same-pass `Transition(to=SUPERSEDED)` for the
  dispatched task. The second half (no re-fire on a later pass) is proven
  by `tests/test_reconcile.py::
  test_ready_to_carve_superseded_is_terminal_no_refire_next_pass`: the same
  task now in `SUPERSEDED` (a follow-up pass's input) with otherwise-empty
  state and `roadmap_exhausted_open=True` (to also suppress item 9's own
  unrelated trigger) yields `actions == []`.
- Additional negative anchor:
  `tests/test_reconcile.py::test_ready_to_carve_no_dispatch_without_frontier_route`
  proves the frontier-review route-health guard (reused from item 9) also
  gates the READY_TO_CARVE handler, not just the in-flight check.

## Deviation from strict `scope.touch`

None. Only `src/nyxloom/reconcile.py`, `tests/test_reconcile.py`, and
`tests/test_invariants.py` were touched (plus this package's own
`nyxloom-trove/reports/P45-{LOG,REPORT}.md`, which are handoff artifacts,
not scope.touch code files). `types.py` was NOT edited.

One in-scope-file finding worth flagging for review even though it required
no scope expansion: `tests/test_invariants.py`'s `KNOWN_STATE_GAPS`
frozenset included `TaskState.READY_TO_CARVE`, and
`test_every_nonterminal_taskstate_is_planned_manual_or_tracked_gap` asserts
`planned.isdisjoint(KNOWN_STATE_GAPS)` where `planned` is a grep-based scan
of `reconcile.py` for `TaskState.\w+` references. Since the fix necessarily
makes `reconcile.py` reference `TaskState.READY_TO_CARVE` in real code,
leaving it in `KNOWN_STATE_GAPS` would fail that assertion. Removed it
(mirroring the file's own precedent for the 2026-07-17 MERGED/VALIDATING
removal) and updated the surrounding stale-doc comments (module docstring
intro, the `KNOWN_STATE_GAPS` block comment, and
`test_task_transition_graph_fully_reachable_from_draft`'s docstring, which
also listed READY_TO_CARVE as "unreachable in practice"). This was
necessary for the suite to stay green, not optional polish.

## Design call not explicitly specified by the oracles (flagged for review)

The READY_TO_CARVE handler's guard is exactly the two conditions O2's
observable text names: no carver in flight, and a healthy frontier-review
route. It deliberately does NOT also gate on `budget_allows` /
`ready_count` / `milestone_admits_work` the way item 9's own trigger does.
Rationale: item 9's extra checks are about whether to proactively top up
the ready-work backlog ahead of a milestone (a throughput throttle); the
READY_TO_CARVE handler is reactive -- resolving one already-existing,
already-rejected task -- a different concern the oracle text doesn't ask to
gate the same way, and the escalate_if bullet on this topic names only the
two predicates as required-to-reuse. Flagging this explicitly in case
review disagrees and wants `budget_allows` added too (cheap to add if so;
none of the current tests would need to change since their default
`budget_remaining` is `None`).

## Regression risk (checked, no changes needed)

Confirmed via grep that no other test file (`test_daemon.py`,
`test_behavioral.py`, `test_cli.py`, `test_properties.py`) exercises a
scenario that would newly hit the changed REJECT LOOP branch or the new
READY_TO_CARVE handler through the REAL `plan_project` (as opposed to a
monkeypatched fake) in a way that would change their expected outcome --
see `nyxloom-trove/reports/P45-LOG.md` for the itemized check. The full
771-passed gate run above confirms this empirically as well.
