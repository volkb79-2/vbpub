# P14 — Stall Detection That Owns a Clock — Implementation Report

**Status:** done · **Date:** 2026-07-15

## Summary

Fixed all six diagnosed failure classes from the two live incident sets
(3 hung `claude --resume` legs undetected for 4.7h; DeepSeek stalls detected
only event-driven at 107/68 min), each with a fix in `adapters.py`/
`reconcile.py`/`daemon.py` and a regression test in the matching test file.
A seventh, closely related gap was discovered and fixed while building the
oracle-1 end-to-end test (see Deviations). All four owned modules' existing
tests remain green; full suite is green (330 passed, up from the ~320
baseline, 0 failures).

## Oracle Results (per handoff/P14-stall-hardening.md)

| # | Oracle | Status | Notes |
|---|--------|--------|-------|
| 1 (headline) | Simulated hang: fake-cli writes one line then sleeps 600; plan emits StallCheck, then ATTEMPT_STALLED (visible event), then InterruptAttempt; after attempts exhausted/no handle, task lands BLOCKED with typed blocker. Driven via `run_pass` with shrunk `stall_log_quiet_seconds=1`, real `wrapper.launch_detached`, real `reconcile.plan_project` | **PASS** | `test_daemon.py::test_hang_detection_full_pipeline_real` |
| 2 | CPU-active-but-quiet-log process (busy child, idle top-level parent) NOT confirmed stalled (tier-2 negative) | **PASS** | `test_daemon.py::test_confirm_stall_cpu_active_child_not_confirmed`; positive companion `test_confirm_stall_idle_process_confirmed_after_two_reads` |
| 3 | claude route argv contains stream-json; `extract_usage` parses a stream-json fixture log (final `result` line) to ACTUAL usage | **PASS** | `test_adapters.py::test_build_dispatch_claude` (amended), `test_build_dispatch_claude_argv_stream_json_exact_position`, `test_extract_usage_stream_json_fixture` |
| 4 | Wall-clock cap: attempt started > cap ago -> InterruptAttempt even with a fresh log (planner test) | **PASS** | `test_reconcile.py::test_wall_clock_cap_exceeded_interrupts_even_with_fresh_log`, `_per_task_budget_override`, `_not_exceeded_no_interrupt` (negative), `_pid_dead_prefers_mark_interrupted` |
| 5 | Full suite green | **PASS** | 330 passed, 0 failed (see Gate Output) |

## Per-Item Fix / Regression-Test Map

| Item | Fix location | Regression test(s) |
|---|---|---|
| 1. Buffered-CLI blindness | `adapters.py::build_dispatch` (claude branch: `stream-json --verbose`, not `json`) | `test_adapters.py::test_build_dispatch_claude` (amended), `test_build_dispatch_claude_argv_stream_json_exact_position`, `test_extract_usage_stream_json_fixture` |
| 2. Silent stall pipeline | `reconcile.py` new `MarkStalled` action + two-phase RUNNING->STALLED->interrupt; `daemon.py` executes it (`ATTEMPT_STALLED`, state STALLED, not ended); `config.py` push_classes +`ATTEMPT_STALLED` | `test_reconcile.py::test_stall_confirmed_marks_stalled_first`, `test_stalled_attempt_then_interrupted`, `test_stalled_attempt_pid_dead_mark_interrupted`; `test_daemon.py::test_mark_stalled_emits_attempt_stalled_not_ended` |
| 3. Tier-2 confirm made real | `daemon.py::_confirm_stall`/new `_proc_cpu_snapshot`/`_proc_children_map` (pid + all descendants via best-effort `/proc` ppid walk); gate-running exemption explicitly NOT implemented (documented, wrapper runs no gates yet) | `test_daemon.py::test_confirm_stall_idle_process_confirmed_after_two_reads`, `test_confirm_stall_cpu_active_child_not_confirmed` |
| 4. Silent dead-end | `reconcile.py::Transition` gained a `blocker` field; INTERRUPTED-with-no-handle-or-exhausted -> `Transition(BLOCKED, blocker=...)`; `daemon.py::_execute` emits `TASK_BLOCKED` (not plain `TASK_TRANSITIONED`) when `blocker` is set | `test_reconcile.py::test_interrupted_no_resume_handle_blocks_task`, `test_interrupted_attempts_exhausted_blocks_task_even_with_handle`; `test_daemon.py::test_transition_to_blocked_emits_task_blocked_with_typed_blocker` |
| 5. Resume bookkeeping drift | `daemon.py` ResumeAttempt execution now also refreshes `attempt.log_path = spec.log_path`; `_attempt_scan` belt-and-braces: falls back to the freshest `wrapper.pid` file on disk when the recorded `attempt.pid` looks dead | `test_daemon.py::test_mark_interrupted_and_resume` (extended with pid/log_path assertions), `test_attempt_scan_wrapper_pid_fallback_recovers_liveness`, `test_attempt_scan_wrapper_pid_fallback_stays_dead` |
| 6. No wall-clock cap | `reconcile.py` new `ReconcileInput.attempt_max_wall_seconds` (default `DEFAULT_ATTEMPT_MAX_WALL_SECONDS=10800`) + `_wall_clock_cap_exceeded` helper (fm.budget.max_wall_seconds overrides); `daemon.py::_build_input` wires it via `getattr(cfg.policy, ..., None) or reconcile.DEFAULT_ATTEMPT_MAX_WALL_SECONDS` | `test_reconcile.py::test_wall_clock_cap_*` (4 tests, see oracle 4 row) |
| (7, related) InterruptAttempt bypassed the wrapper's own signal handler | `daemon.py::_execute` InterruptAttempt now signals the WRAPPER's own pid (`wrapper.pid` file) first, falling back to `child.pid`'s pgid only if the wrapper is already gone | `test_daemon.py::test_hang_detection_full_pipeline_real` (exercises it end-to-end); `test_interrupt_attempt_signals_pgid` (pre-existing, still green — it never writes a `wrapper.pid` file, so it exercises exactly the fallback branch) |

## Files Touched

- `src/handoffctl/adapters.py` — claude dispatch argv: `stream-json --verbose` instead of buffered `json`; docstring updated
- `src/handoffctl/reconcile.py` — `MarkStalled` action, `Transition.blocker` field, `_wall_clock_cap_exceeded` helper, `ReconcileInput.attempt_max_wall_seconds`/`DEFAULT_ATTEMPT_MAX_WALL_SECONDS`, restructured attempt-actions loop (receipt -> pid-dead -> wall-clock -> stalled-confirmed-interrupt -> stall-detect -> interrupted-handling), rewrote the item-4 INTERRUPTED branch; docstring updated throughout
- `src/handoffctl/daemon.py` — `MarkStalled`/typed-blocker `Transition` execution branches, `ResumeAttempt` pid+log_path refresh, `_attempt_scan` wrapper.pid fallback, `_confirm_stall`/`_proc_cpu_snapshot`/`_proc_children_map` (descendant-aware tier-2), `_build_input` wires `attempt_max_wall_seconds`, `InterruptAttempt` now signals wrapper.pid first (see item 7); docstring (EXECUTION MAP + input-building) updated throughout
- `src/handoffctl/config.py` — `NotifyConfig.push_classes` gained `"ATTEMPT_STALLED"` (the one authorized frozen-file edit)
- `src/handoffctl/wrapper.py` — documentation-only: notes the gate-running-marker exemption is intentionally not implemented (no gates wired into the wrapper yet); no functional change
- `tests/test_adapters.py` — amended `test_build_dispatch_claude` (stream-json, not buffered json) + 2 new tests
- `tests/test_reconcile.py` — imports extended (`MarkStalled`, `Blocker`/`BlockerType`), `make_frontmatter` gained a `budget` param, amended `test_stall_confirmed_interrupt` -> `test_stall_confirmed_marks_stalled_first` + 12 new tests (stall two-phase, dead-end, wall-clock cap)
- `tests/test_daemon.py` — `import signal`, `_drive_until` helper, 9 new tests (typed-blocker transition, MarkStalled execution, attempt_scan pid fallback x2, confirm_stall real CPU x2, headline hang-detection pipeline) + extended `test_mark_interrupted_and_resume` with pid/log_path assertions
- `tests/test_wrapper.py` — untouched (no wrapper.py behavior changed; confirmed via `git diff --stat`, 0 lines changed)

## Gate Output (tail)

Scoped gate (the four matching test files):

```
cd /workspaces/vbpub/handoffctl2 && /workspaces/vbpub/.venv/bin/python -m pytest tests/test_adapters.py tests/test_reconcile.py tests/test_daemon.py tests/test_wrapper.py -q
........................................................................ [ 58%]
....................................................                     [100%]
124 passed in 44.15s
```

Full suite (`tests/`):

```
cd /workspaces/vbpub/handoffctl2 && /workspaces/vbpub/.venv/bin/python -m pytest tests/ -q
........................................................................ [ 21%]
........................................................................ [ 43%]
........................................................................ [ 65%]
........................................................................ [ 87%]
..........................................                               [100%]
330 passed in 59.86s
```

## Deviations or Assumptions

- **`attempt_max_wall_seconds` NOT added to `config.Policy`.** The handoff's
  item 6 says "add `attempt_max_wall_seconds` to Policy, default 10800", but
  the package Rules trailer restricts `config.py` edits to exactly the one
  `NotifyConfig.push_classes` entry (item 2). Since `config.py`'s `Policy` is
  a strict dataclass (no `**kwargs`), adding a field there would violate that
  restriction. Implemented instead as `reconcile.DEFAULT_ATTEMPT_MAX_WALL_SECONDS`
  (module constant) + `ReconcileInput.attempt_max_wall_seconds` (new field on
  the package's own, unfrozen dataclass), wired in `daemon.py::_build_input`
  via `getattr(cfg.policy, "attempt_max_wall_seconds", None) or
  reconcile.DEFAULT_ATTEMPT_MAX_WALL_SECONDS` — forward-compatible (a future
  `Policy.attempt_max_wall_seconds` field would take effect with zero code
  change here) without touching the frozen file beyond the one authorized line.
  All oracle-4 behavior is satisfied either way. Flagging for the reviewer in
  case per-project TOML configurability of this cap is wanted sooner.
- **Item 4's `Transition` action gained a `blocker: Blocker | None = None`
  field** rather than introducing a new `BlockTask` action type, to keep the
  action set minimal and follow the existing pattern of `daemon.py::_execute`
  branching on payload shape (as already done for `EmitAttemptExit`'s
  receipt-result branching). `daemon.py`'s `Transition` execution now emits
  `TASK_BLOCKED` (not `TASK_TRANSITIONED`) when `action.to is BLOCKED and
  action.blocker is not None`.
- **Item 3's gate-running exemption (v2 §5.4) is intentionally NOT
  implemented** — the handoff explicitly permits skipping it "with a code
  comment if wrapper gates are not yet wired (they are not — receipts show
  oracles [])"; confirmed via `wrapper.py`'s own docstring
  ("the wrapper does NOT run gates"). Documented in `daemon.py::_confirm_stall`'s
  docstring and `wrapper.py`'s Oracles bullet.
- **A seventh, closely related gap was found and fixed while building the
  oracle-1 end-to-end test**, not one of the six numbered items but required
  for InterruptAttempt to ever actually close the loop: `daemon.py`'s
  pre-existing `InterruptAttempt` execution sent `SIGTERM` directly to the
  CLI child's own process group (via `child.pid`), which bypasses the
  wrapper's own installed signal handler entirely — the handler is what
  forwards `SIGTERM` to the child AND classifies the resulting exit as
  `'interrupted'` (confirmed against `wrapper.py`'s own real-signal tests in
  `test_wrapper.py`, which signal `wrapper_pid` directly, never `child.pid`).
  Without this fix, a real interrupted CLI would misclassify as a plain
  `'error'` exit and the confirmed-stall pipeline would silently retry
  instead of ever reaching `INTERRUPTED` -> `BLOCKED`. Fixed by signaling
  `wrapper.pid` first, falling back to `child.pid`'s pgid only if the
  wrapper process is already gone (crashed). The pre-existing
  `test_interrupt_attempt_signals_pgid` test still passes unmodified — it
  never writes a `wrapper.pid` file, so it exercises exactly the fallback
  branch. Flagging this prominently since it's outside the six diagnosed
  classes but was blocking oracle 1.
- **`test_hang_detection_full_pipeline_real` deletes the `sample_project`
  fixture's own `demo-P01-sample.md` handoff** before running, since this
  test deliberately runs the REAL (unmonkeypatched) `reconcile.plan_project`
  — leaving that handoff in place would have it auto-`CreateTask`d ->
  `QUEUED` -> dispatched through the literal `"fake"` executable (not a real
  binary on `PATH`), an unrelated async failure this test doesn't need.
- Both new `test_daemon.py` tier-2 tests and the headline pipeline test spawn
  real subprocesses (`sleep`, a busy `sh` loop, the `hang.sh` fixture script)
  and poll with short sleeps (<= 0.5s per call, matching existing real-signal
  test conventions in `test_wrapper.py`); total wall time for the new tests
  is a few seconds each, well within the existing suite's real-process test
  norms.

## Suggestions for the Reviewer (informational only — not acted on)

- Consider whether `routes.host.toml`'s live `claude-sonnet5-high.resume`
  template should also gain `--output-format stream-json --verbose` (item 1
  mentions "dispatch/resume argv"); `build_resume` is purely template-driven
  from route config data, not CLI-shape logic in `adapters.py`, and
  `routes.host.toml` is not an owned file for this package, so it was left
  untouched. Currently only the *dispatch* (initial launch) argv gets the
  fix; a *resumed* claude leg still launches via whatever
  `routes.host.toml`'s `resume` template specifies.
- The `_proc_cpu_snapshot` descendant walk in `daemon.py` does one full
  `/proc` scan per confirmation check per attempt; fine at pilot scale
  (few concurrent attempts, one host) but would want batching if the number
  of concurrently-tracked attempts grows large.
- `MarkStalled`'s daemon-side event carries the full attempt dict via the
  normal `ATTEMPT_*` upsert projection; no new payload shape was needed.
