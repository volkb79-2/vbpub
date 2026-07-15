# P02 — reconcile planner (pure scheduler logic)

> Tier: haiku · Depends-on: none · Read first: handoff/STANDING.md,
> src/handoffctl/reconcile.py (docstring = the normative semantics, items
> 1–8), src/handoffctl/types.py, docs/SPEC.md §5 §8 §9.

## Owned files
- `src/handoffctl/reconcile.py` (implement `plan_project` and
  `dispatch_eligible`; the Action/ReconcileInput dataclasses are frozen —
  do not add/remove fields)
- `tests/test_reconcile.py`

## Objective
Implement the planner exactly per the module docstring. It is PURE: no
imports beyond what the stub has, no filesystem, no time.time() (use
`inp.now`). Tests construct ReconcileInput by hand (no fixtures needed
beyond plain object construction; `sample_project` may provide a cfg).

## Oracles (each a named test; build minimal inputs)
1. **create**: frontmatter id absent from states → exactly one CreateTask;
   next pass (statefile CARVED present, lint_clean True) → Transition to
   QUEUED. lint_clean False → NO transition (negative).
2. **decision-hold**: QUEUED task, depends_on ['D-007'], 'D-007' in
   decisions_open → Transition NEEDS_DECISION with notes containing
   'D-007'. NEEDS_DECISION task with decisions_open empty → Transition
   QUEUED. A task with an open D-dep is never dispatched (negative).
3. **dispatch-order**: three QUEUED eligible tasks, max_active_tasks=2,
   zero active → exactly 2 DispatchImplementer in sorted task-id order,
   each with the first provider_ok route of the tier; first route
   provider_ok False → second route chosen; all routes False →
   no dispatch and `dispatch_eligible` reason 'no-healthy-route'.
4. **caps**: one ACTIVE task + max_active_tasks=1 → zero dispatches
   (reason 'wip-cap'). attempts == max_attempts_per_task → reason
   'attempts-exhausted'. budget_remaining=0.0 → 'budget-exhausted'.
   paused task → 'paused'; project_paused → 'paused' for all.
5. **deps**: depends_on task neither COMPLETED nor branch-merged →
   'deps-unmerged:<id>'; COMPLETED dep passes; branch in merged_branches
   passes.
6. **mutex**: fm stack 'exclusive', leases_free {'demo.stack': False} →
   'lease-unavailable:demo.stack' (lease name via
   cfg.mutexes['stack'].lease_name('demo')).
7. **receipt**: RUNNING attempt with receipts[att] set → EmitAttemptExit
   (and nothing else for that attempt). pid dead, no receipt →
   MarkInterrupted. INTERRUPTED attempt with session_handle →
   ResumeAttempt; without handle and attempts-budget left →
   DispatchImplementer (fresh).
8. **stall**: pid alive, no receipt, log_quiet_seconds >
   policy.stall_log_quiet_seconds → StallCheck; stall_confirmed True →
   InterruptAttempt (and no StallCheck). Quiet below threshold → neither
   (negative).
9. **waves**: 3 AWAITING_REVIEW unwaved tasks, wave_max_diffs 3 →
   one OpenWave with all 3 sorted; 2 waiting but oldest.since older than
   wave_open_after_seconds → OpenWave with 2; 1 waiting, fresh → none.
   A wave already opened (tasks carry wave_id) with no FRONTIER_REVIEW
   attempt RUNNING among them → LaunchReview with that wave_id.
10. **ratchet**: merge_history = 3×(id, 0, 'review'),
    max_consecutive_zero_progress_merges=3, ratchet_already_open False →
    exactly one SpecAttention(reason='ratchet'). Any tuple with units>0 or
    source 'roadmap', or ratchet_already_open True → none (negatives).
11. **spec-health**: carve_outcomes [{'outcome': 'SPEC_GAP'}] →
    SpecAttention('carve-outcome'); review_rejections_by_area {'ui': 2} →
    SpecAttention('rejections'); blocked_underspecified_count 3 →
    SpecAttention('blocked-underspecified').
12. **determinism**: one composite input exercising 1+3+9 → two calls
    return equal `[repr(a) for a in actions]` lists, and the ordering
    contract (lifecycle sorted-by-task, then attempts, then waves, then
    SpecAttention) holds — assert the exact sequence.

## Guidance
- Compute `active_count` as tasks in {ACTIVE, AWAITING_REVIEW} — dispatch
  capacity counts both (they hold worktrees/slots).
- `dispatch_eligible` is the single source for item-3/4/5/6 checks;
  plan_project MUST call it (test by monkeypatching it in one test and
  asserting the plan honors the stub's False).
- Attempts count toward 'attempts-exhausted' EXCLUDING attempts whose
  receipt result was 'limit' (docstring item 4).
- Helper predicates may be module-private (`_`-prefixed).
