# P85 Work Log

## Context

- Branch: feat/topos-p85-flaky-ui-timing-gate
- Worktree: .worktrees/topos-p85-flaky-ui-timing-gate
- Base commit: e9298a8 (carve: P84/P85 -- review-derived; P82 superseded)
- Package: P85 - Flaky UI timing gate
- Current objective: Repair two flaky UI timing tests so the gate is readable

## Timeline

```text
2026-07-13 18:45 UTC
- Action: Explore handoff documents and both flaky tests.
- Commands: read_file topos/handoff/P85-flaky-ui-timing-gate.md, read_file topos/tests/test_ui_app.py, read_file topos/tests/test_record.py, read_file topos/src/topos/ui/app.py
- Files changed: (none yet)
- Result: Understood both flaky tests and their async mechanisms.
- Follow-up: Reproduce flakiness.

2026-07-13 18:47 UTC
- Action: Reproduce flakiness on test_pilot_snapshot_running_status_appears_immediately.
- Commands: for i in $(seq 1 10); do python3 -m pytest topos/tests/test_ui_app.py::test_pilot_snapshot_running_status_appears_immediately -x -q; done
- Result: 4/10 failed — confirmed ~1-in-3 flake rate.
- Follow-up: Measure timing of pilot.pause() vs worker thread.

2026-07-13 18:50 UTC
- Action: Measure elapsed time of 30 pilot.pause() calls with actual snapshot worker running.
- Commands: PYTHONPATH=... python3 -c 'asyncio.run(measure())'  (probe with slow_systemctl 0.5s sleep)
- Result: 30 pauses complete in ~0.79s wall-clock, but the 0.5s thread sleep + overhead means the worker may not finish in time. Diagnosis confirmed: the fixed-iteration pilot.pause() loop races against wall-clock time.

2026-07-13 18:55 UTC
- Action: Implement fix — replace fixed-iteration pilot.pause() loops with wall-clock deadline.
- Files changed: topos/tests/test_ui_app.py (add _wait_or_timeout helper, use it), topos/src/topos/ui/app.py (fix _run_ui_smoke)
- Result: Both tests compile and pass.
- Follow-up: Mutation testing.

2026-07-13 18:58 UTC
- Action: Mutation tests — break each behavior under test and verify test turns red.
- Mutation 1: Comment out _refresh_status("snapshot running:...") → test fails on assert.
- Mutation 2: Comment out _snapshot_in_progress = False → test fails with deadline-based AssertionError.
- Mutation 3: Block frame arrival in _run_ui_smoke → record CLI test fails on stdout mismatch.
- Result: All mutations produce red tests.
- Follow-up: Stress tests.

2026-07-13 19:02 UTC
- Action: Run each repaired test 20x consecutively.
- Commands: for i in $(seq 1 20); do python3 -m pytest ... ; done (for each test)
- Result: Test 1: 20/20 green. Test 2: 20/20 green.
- Follow-up: Full suite runs.

2026-07-13 19:10 UTC
- Action: Run full suite 3x consecutively.
- Commands: timeout 900 python3 -m pytest topos/tests -q (3 times)
- Result: Run 1: 1331 passed, 8 skipped. Run 2: 1331 passed, 8 skipped. Run 3: 1339 passed. Zero failures.
- Follow-up: Write LOG and REPORT.
```

## Decisions

- Decision: Use wall-clock deadline (asyncio.get_event_loop().time() + timeout) instead of fixed iteration count for pilot.pause() loops.
  Reason: pilot.pause() yields to the event loop and returns immediately when idle. Fixed iteration counts correspond to event-loop ticks, not wall-clock time. The worker thread runs in wall-clock time (time.sleep, CPU work), so the polling side must also use wall-clock time to avoid the race.
  Impact: Polling loops are now bounded by real timeout (e.g. 10s) instead of iteration count. Under normal conditions they return much faster; under extreme load they fail honestly with an AssertionError rather than silently timing out.
- Decision: This is a test-timing artifact, not a product race.
  Reason: The synchronous status update in action_create_snapshot (line 431) sets the widget content immediately. A real user sees it on the next screen refresh. The async completion via call_from_thread works correctly when the event loop gets enough real time. The 30-iteration loop was simply not enough wall-clock time under load.

## Validation

```bash
# Test 1: 20x isolation stress
for i in $(seq 1 20); do
  python3 -m pytest topos/tests/test_ui_app.py::test_pilot_snapshot_running_status_appears_immediately -x -q 2>&1 | tail -1
done
# 20/20 passed

# Test 2: 20x isolation stress
for i in $(seq 1 20); do
  python3 -m pytest topos/tests/test_record.py::test_record_cli_runs_ui_and_writes_frames -x -q 2>&1 | tail -1
done
# 20/20 passed

# Full suite × 3
timeout 900 python3 -m pytest topos/tests -q
# Run 1: 1331 passed, 8 skipped
# Run 2: 1331 passed, 8 skipped
# Run 3: 1339 passed

# Mutation 1: snapshot running: status never set
sed -i '431s/self._refresh_status/# self._refresh_status/' topos/src/topos/ui/app.py
python3 -m pytest topos/tests/test_ui_app.py::test_pilot_snapshot_running_status_appears_immediately -x -q
# FAILED: AssertionError: 'snapshot running:' not in status text
sed -i '431s/# self._refresh_status/self._refresh_status/' topos/src/topos/ui/app.py

# Mutation 2: _snapshot_in_progress never reset
sed -i '469s/self._snapshot_in_progress = False/# self._snapshot_in_progress = False/' topos/src/topos/ui/app.py
timeout 15 python3 -m pytest topos/tests/test_ui_app.py::test_pilot_snapshot_running_status_appears_immediately -x -q
# FAILED: AssertionError: predicate not satisfied within 10.0s
sed -i '469s/# self._snapshot_in_progress = False/self._snapshot_in_progress = False/' topos/src/topos/ui/app.py

# Mutation 3: frame never arrives in _run_ui_smoke
sed -i '636,640s/if app.frames_received:/if False:/' topos/src/topos/ui/app.py
timeout 20 python3 -m pytest topos/tests/test_record.py::test_record_cli_runs_ui_and_writes_frames -x -q
# FAILED: AssertionError on stdout mismatch
git checkout topos/src/topos/ui/app.py  # revert — re-applied fix afterward
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
