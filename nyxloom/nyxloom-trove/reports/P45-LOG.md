# P45 — LOG (Close the READY_TO_CARVE dead-end)

Branch: `feat/nyxloom-P45-ready-to-carve-triage`
Worktree: `/workspaces/vbpub/nyxloom/.worktrees/nyxloom-P45-ready-to-carve-triage/nyxloom`
Input revision: `f38d306` (post-P44 main)

## Context read (per handoff's "Context to read first")

- `src/nyxloom/reconcile.py` L1-171 (module docstring, contract items 1-11)
- `src/nyxloom/reconcile.py` ~L412-497 (REJECT LOOP block, KNOWN GAP comment)
- `src/nyxloom/reconcile.py` ~L790-909 (CARVE TRIGGER, module contract item
  9 -- the `carve_in_flight` / `frontier_route_available` predicates to reuse)
- `src/nyxloom/reconcile.py` `CarveDispatch` dataclass (~L284 area pre-edit)
- `src/nyxloom/types.py` L45-97 (`TaskState`, `TERMINAL_TASK_STATES`,
  `TASK_TRANSITIONS`) -- confirmed `REVIEW_REJECTED -> READY_TO_CARVE` and
  `READY_TO_CARVE -> SUPERSEDED` are both legal edges in the frozen table.
  types.py NOT edited.
- `src/nyxloom/daemon.py` L1656-1741 (`_execute_carve_dispatch`) -- confirmed
  it NEVER reads `action.task_id` (only `action.item_id`/the `project`
  param passed separately); it always mints its own fresh synthetic
  `carve-{project}-{seq}` task. So setting `task_id=fm_id` on the new
  READY_TO_CARVE-triggered CarveDispatch is purely informational/self-
  describing (matches the existing `project` field's own rationale in the
  dataclass docstring) -- NOT a second execution path.
- `src/nyxloom/daemon.py` L1470-1553 (`_carve_source_note_lines` /
  `_targeted_item_note_lines`) -- confirmed `item_id=None` takes the
  untargeted path, which already embeds "recent REVIEW_RECORDED follow-ups"
  automatically (via `_recent_review_follow_ups`) -- this is why O2 says
  leave `item_id=None`.
- `tests/test_invariants.py` L1-62 (module docstring), L157-200 (`_base_input`,
  `_action_touches_task` helpers), L255-330 (`KNOWN_STATE_GAPS` + coverage
  test), L394-431-ish (the pinned xfail test this package closes).
- `tests/test_reconcile.py` -- skimmed existing REJECT LOOP tests
  (`test_review_rejected_with_budget_remaining_requeues`,
  `test_review_rejected_attempts_exhausted_documents_known_gap`) and the
  CARVE TRIGGER test section (`make_carve_routes`, `_carve_base_kwargs`,
  `test_carve_trigger_*`) to reuse helpers/conventions.

## Decisions

1. **Shared single-carve-authority guard.** Computed `carve_in_flight` and
   `frontier_route_available` ONCE in `plan_project`, right after the
   existing per-task lifecycle loop (item 2) and before both the new
   READY_TO_CARVE handler and the pre-existing item-9 headroom-refill
   trigger. Introduced a `carve_dispatch_planned` flag, set True by
   whichever of the two blocks fires first (the READY_TO_CARVE handler runs
   earlier in the function body, so it wins ties within a pass). Item 9's
   block now additionally guards on `not carve_dispatch_planned` and no
   longer recomputes `carve_in_flight`/`frontier_route_available` itself.
   This is the O3 mechanism: AT MOST ONE CarveDispatch per pass, shared.

2. **REJECT LOOP (O1).** Added an `else` branch to the existing
   `if tsf.state == TaskState.REVIEW_REJECTED:` block: when
   `rejected_attempts_count >= max_attempts_per_task`, plan
   `Transition(task_id=fm_id, to=TaskState.READY_TO_CARVE, notes=...)`
   instead of the prior silent no-op. Updated the surrounding "KNOWN GAP"
   comment (it documented an unfixed absence; now describes the fix) and
   the module docstring's item 10 entry.

3. **READY_TO_CARVE handler (O2/O3/O4, new module contract item 12).** Added
   a dedicated block (sorted `task_id` order, mirrors item 3's dispatch-
   capacity loop determinism) that, when `not carve_in_flight and
   frontier_route_available`, picks the single lowest-sorted READY_TO_CARVE
   task_id and appends BOTH:
   - `CarveDispatch(project=inp.cfg.project_id, task_id=chosen_id)` --
     `item_id` left `None` (untargeted path, per escalate_if guidance:
     `item_id` is a `backlog_items.parse` key, which a rejected TASK id is
     not).
   - `Transition(task_id=chosen_id, to=TaskState.SUPERSEDED, notes=...)` --
     self-limiting, same pass, so it never re-fires a second CarveDispatch
     once the carve slot frees up later.

   Deliberately did NOT add `budget_allows`/`ready_count`/
   `milestone_admits_work` checks to this handler -- O2's observable text
   names exactly two conditions ("no carver attempt already in flight and a
   healthy frontier-review route exists"), and the escalate_if bullet only
   names those same two predicates as required-to-reuse. Item 9's own
   ready-count/milestone/budget checks are about whether to proactively
   top up the backlog ahead of a milestone; the READY_TO_CARVE handler is
   reactive (resolving one already-existing, already-rejected task), a
   different concern the oracle doesn't ask to gate the same way. Noted
   here for review rather than silently added or silently omitted.

4. **`CarveDispatch` dataclass docstring** updated: `task_id` is no longer
   "always None" (item 12 sets it); clarified it's informational only,
   `_execute_carve_dispatch` never reads it.

5. **`tests/test_invariants.py` `KNOWN_STATE_GAPS`.** Discovered (NOT called
   out explicitly in the handoff, but load-bearing): this frozenset
   included `TaskState.READY_TO_CARVE`, and
   `test_every_nonterminal_taskstate_is_planned_manual_or_tracked_gap`
   asserts `planned.isdisjoint(KNOWN_STATE_GAPS)` where `planned` is a
   grep-based scan of `reconcile.py` for `TaskState.\w+` references. Since
   my new code now genuinely references `TaskState.READY_TO_CARVE` (both in
   the REJECT LOOP's new Transition target and the new handler's state
   check), leaving it in `KNOWN_STATE_GAPS` would make that assertion FAIL
   (READY_TO_CARVE now in both sets). Removed it from `KNOWN_STATE_GAPS`
   (mirroring the exact precedent already in this file's comments for the
   2026-07-17 MERGED/VALIDATING removal) and updated the surrounding
   comments/docstrings for accuracy (module docstring intro, the
   `KNOWN_STATE_GAPS` block comment, and
   `test_task_transition_graph_fully_reachable_from_draft`'s docstring,
   which also listed READY_TO_CARVE as "unreachable in practice").

6. **`test_no_dead_end_ready_to_carve`.** Removed the `xfail(strict=True)`
   marker. Required a `routes` override (`_routes(tier="frontier-review")`)
   since `_base_input`'s default `Routes` only registers a `flash-high`
   tier, and my handler's guard specifically checks the `frontier-review`
   tier (same as item 9). This is the escalate_if-anticipated fixture
   adjustment ("you may need to adjust its `_base_input` fixture if it
   lacks a field your predicate needs") -- resolved by passing an override
   at the call site rather than changing `_base_input`'s shared default
   (which many other tests in that file depend on).

7. **`tests/test_reconcile.py`.** Repurposed
   `test_review_rejected_attempts_exhausted_documents_known_gap` (renamed
   `test_review_rejected_attempts_exhausted_routes_to_ready_to_carve`) to
   assert the new `Transition(to=READY_TO_CARVE)` instead of `transitions
   == []` -- this IS the O1(a) non-hollow anchor the oracle asks for; left
   `test_review_rejected_with_budget_remaining_requeues` (O1(b)'s regression
   pin) completely untouched. Added five new tests for O2/O3/O4 in a new
   section at the end of the file, reusing `make_carve_routes`/
   `_carve_base_kwargs` from the existing CARVE TRIGGER test section.

## Regression risk check (done before running the gate)

Grepped all test files for `REVIEW_REJECTED`/`READY_TO_CARVE`/
`CarveDispatch`/`plan_project` usage outside the two in-scope test files:
- `tests/test_daemon.py`'s REVIEW_REJECTED tests all monkeypatch
  `reconcile.plan_project` via a `_scripted()` helper -- they never call the
  real implementation, so unaffected.
- `tests/test_behavioral.py::test_reject_loop_requeues_never_strands` drives
  the REAL reconcile loop, but its scripted scenario only rejects the task
  ONCE against `max_attempts_per_task = 20` -- never hits the
  exhausted-budget branch, so unaffected.
- `tests/test_cli.py`'s REVIEW_REJECTED tests exercise the CLI's own
  reject/requeue command + direct `storage.append_and_apply`, never
  `plan_project` -- unaffected.
- `tests/test_reconcile.py::test_determinism_composite_input` has no
  `frontier-review` tier registered in its `routes`, so
  `frontier_route_available` is False regardless of my change --
  unaffected.
- `tests/test_properties.py` has zero references to any of these symbols.

## Files changed

- `src/nyxloom/reconcile.py` (module docstring items 9/10/+12,
  `CarveDispatch` docstring, REJECT LOOP block, new READY_TO_CARVE handler,
  item-9 block updated to share the guard)
- `tests/test_reconcile.py` (renamed/updated exhausted-budget test, 5 new
  READY_TO_CARVE tests)
- `tests/test_invariants.py` (`KNOWN_STATE_GAPS` fix, xfail removal +
  routes override, stale-doc comment updates)

No files outside `scope.touch` were touched. No `types.py` edits.

## Next: run the gate (tester-unified), then write P45-REPORT.md.
