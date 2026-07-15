# P02 — reconcile planner (pure scheduler logic) — REPORT

**Status:** done  
**Date:** 2026-07-15

## Summary

Package P02 implements the pure reconcile planner functions that compute deterministic action plans for project scheduling without filesystem, subprocess, or storage side effects. All 12 oracles are implemented and green.

## Implementation

### Files touched

1. **`src/handoffctl/reconcile.py`**
   - Implemented `plan_project(inp: ReconcileInput) -> list[Action]`
   - Implemented `dispatch_eligible(fm: Frontmatter, tsf: TaskStateFile, inp: ReconcileInput) -> tuple[bool, str]`
   - All semantics from module docstring items 1–8 encoded exactly
   - Output order: task lifecycle actions (sorted by task_id), attempt actions, waves, SpecAttention

2. **`tests/test_reconcile.py`** (created)
   - 35 comprehensive tests covering all 12 oracles
   - Tests cover positive and negative cases per oracle contract
   - Minimal inputs, observable assertions, no hollow tests

### Implementation details

#### `plan_project(inp: ReconcileInput) -> list[Action]`

Implements the 8-point scheduler contract:

1. **NEW HANDOFFS**: frontmatter id absent → CreateTask; CARVED → QUEUED when lint_clean True
2. **DECISION HOLDS**: QUEUED + open D-dep → Transition to NEEDS_DECISION; NEEDS_DECISION + resolved → Transition back to QUEUED
3. **DISPATCH**: Eligible QUEUED tasks dispatched up to `max_active_tasks - active_count` capacity, in sorted task_id order, selecting first healthy route per tier
4. **RUNNING ATTEMPTS**: Receipt present → EmitAttemptExit; no receipt + pid dead → MarkInterrupted; INTERRUPTED + session_handle → ResumeAttempt; log quiet + pid alive → StallCheck or InterruptAttempt per stall_confirmed
5. **REVIEW WAVES**: AWAITING_REVIEW unwaved tasks batched into OpenWave when count ≥ wave_max_diffs or oldest age > wave_open_after_seconds; LaunchReview for waves without FRONTIER_REVIEW RUNNING
6. **PROGRESS RATCHET**: Consecutive zero-progress review merges at threshold → SpecAttention('ratchet') once
7. **SPEC HEALTH**: Carve outcomes SPEC_GAP, review rejections ≥2 per area, blocked_underspecified ≥3 → SpecAttention variants
8. **ORDERING**: Deterministic output: lifecycle sorted-by-task-id, then attempts, then waves, then SpecAttention

#### `dispatch_eligible(fm: Frontmatter, tsf: TaskStateFile, inp: ReconcileInput) -> tuple[bool, str]`

Single source of truth for dispatch checks (items 3–6 + 7–8), ordered:

1. Paused (task or project) → 'paused'
2. Task deps: must be COMPLETED or branch merged → 'deps-unmerged:<id>'
3. Decision deps: none open → 'decision-hold:<D-id>'
4. Active count < max_active_tasks → 'wip-cap'
5. Attempts (excluding 'limit' results) < max_attempts → 'attempts-exhausted'
6. Budget remaining None or > 0 → 'budget-exhausted'
7. All mutexes' leases free → 'lease-unavailable:<name>'
8. Healthy route exists for tier → 'no-healthy-route'

Returns `(True, '')` when all checks pass.

### Test coverage — 12 oracles

| Oracle | Test(s) | Status |
|--------|---------|--------|
| 1. create | test_create_new_frontmatter, test_create_carved_to_queued, test_create_carved_lint_false_no_transition | ✓ 3/3 |
| 2. decision-hold | test_decision_hold_queued_with_open_decision, test_decision_hold_needs_decision_with_resolved, test_decision_hold_never_dispatched | ✓ 3/3 |
| 3. dispatch-order | test_dispatch_order_three_tasks_max_two, test_dispatch_first_route_unhealthy, test_dispatch_no_healthy_route | ✓ 3/3 |
| 4. caps | test_caps_wip_cap_one_active, test_caps_attempts_exhausted, test_caps_budget_exhausted, test_caps_paused_task, test_caps_project_paused | ✓ 5/5 |
| 5. deps | test_deps_unmerged_dep_blocked, test_deps_completed_passes, test_deps_branch_merged_passes | ✓ 3/3 |
| 6. mutex | test_mutex_stack_exclusive_lease_unavailable | ✓ 1/1 |
| 7. receipt | test_receipt_running_with_receipt_emits_exit, test_receipt_pid_dead_no_receipt_mark_interrupted, test_receipt_interrupted_with_session_handle_resume | ✓ 3/3 |
| 8. stall | test_stall_check_log_quiet_over_threshold, test_stall_confirmed_interrupt, test_stall_quiet_below_threshold | ✓ 3/3 |
| 9. waves | test_waves_three_awaiting_review_opens_wave, test_waves_oldest_over_timeout_opens, test_waves_fresh_single_no_open, test_waves_launch_review_no_running_attempt | ✓ 4/4 |
| 10. ratchet | test_ratchet_zero_progress_review_merges, test_ratchet_units_positive_no_attention, test_ratchet_roadmap_source_no_attention | ✓ 3/3 |
| 11. spec-health | test_spec_health_carve_outcome_spec_gap, test_spec_health_review_rejections, test_spec_health_blocked_underspecified | ✓ 3/3 |
| 12. determinism | test_determinism_composite_input | ✓ 1/1 |

**Total: 35 tests, 35 pass, 0 fail**

### Test strategy

- **Minimal inputs**: Each test constructs only required fields via helper functions (`make_config`, `make_frontmatter`, `make_tsf`, etc.), avoiding unnecessary dependencies
- **Observable artifacts**: Tests assert on action types and fields (task_ids, route_ids, reasons), not call bookkeeping
- **Negative cases**: Every bound/negative case in the oracles has a test that violates it and asserts the outcome (e.g., lint_clean False blocks CARVED→QUEUED, open D-dep blocks dispatch)
- **Determinism**: Composite test (oracle 12) exercises interacting rules (1+3+9) and verifies output order and exact reproducibility
- **No hollow tests**: Each test verifies an observable mechanism (dispatch cap, decision hold blocking, receipt handling, etc.)

## Verification

### Gate command

```
cd /workspaces/vbpub/handoffctl2 && /workspaces/vbpub/.venv/bin/python -m pytest tests/test_reconcile.py -q
```

### Output (verbatim tail)

```
============================= test session starts ==============================
platform linux -- Python 3.13.5, pytest-9.1.1, pluggy-1.6.0
rootdir: /workspaces/vbpub/handoffctl2
configfile: pyproject.toml
plugins: hypothesis-6.156.6, cov-7.1.0, anyio-4.14.2, asyncio-1.4.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None
collected 35 items

tests/test_reconcile.py ...................................              [100%]

============================== 35 passed in 0.14s ==============================
```

## Deviations & assumptions

- **Attempts count**: Sum of attempts with terminal state AND receipt result != 'limit', as per docstring item 4. Tests confirm 'limit' receipts don't consume retry budget.
- **Active count**: Tasks in {ACTIVE, AWAITING_REVIEW} state count toward dispatch capacity, as stated in guidance (both hold worktree/slots).
- **Route selection order**: Routes returned by `Routes.for_tier()` in declared order; first healthy (provider_ok True) is selected per item 3.
- **Merge history interpretation**: Most recent first (list head), checked against max_consecutive_zero_progress_merges as the window size per item 6.
- **Wave opening**: Separate logic for "threshold reached" (count ≥ wave_max) vs. "oldest aged" (since > now - wave_open_after_seconds); either triggers OpenWave. LaunchReview emitted only once per wave (no FRONTIER_REVIEW attempt RUNNING).
- **Frozen dataclasses**: Action, ReconcileInput, Frontmatter, TaskStateFile match frozen signatures exactly; no fields added/removed.

## Notes for reviewer

1. **purity contract**: Module contains zero filesystem, subprocess, or time.time() calls; all timing via `inp.now` snapshot.
2. **Single source of truth for dispatch**: `dispatch_eligible` is called by `plan_project` and exercised directly in tests; tests monkeypatch it once to verify plan_project honors False returns.
3. **Determinism**: Output order is rigid (lifecycle sorted by task_id, then attempts, then waves, then SpecAttention); oracle 12 test verifies two plan_project calls on identical input produce identical `[repr(a) for a in actions]` lists.
4. **No scaffolding**: All code is production logic; no debug prints, no unused helpers, ASCII only.
5. **Type hints**: Public functions fully type-hinted; private helpers (`_merge_history_check`, etc.) follow conventions.

---

**Implementation completed by:** Claude Haiku 4.5  
**Date completed:** 2026-07-15 23:59:59 UTC  
**Next package:** Ready for review and merge (no further work required for P02).
