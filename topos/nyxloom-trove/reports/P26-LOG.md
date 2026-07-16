# P26 Work Log

Keep this file current while working. It is a resumability artifact, not a
place for private reasoning. Record observable actions, decisions, and next
steps so another controller can continue after a session limit.

## Context

- Branch: feat/topos-p26-snapshot-progress-ui
- Worktree: .worktrees/-topos-p26-snapshot-progress-ui
- Base commit: 48f2cbf (docs(topos): carve P26 snapshot progress handoff)
- Package: P26 Snapshot progress UI
- Current objective: Implement async snapshot worker with progress/status UI, duplicate-start guard, focused tests, docs updates

## Timeline

```text
2026-07-09
- Action: Created worktree + feature branch from main.
- Commands: git worktree add -b feat/topos-p26-snapshot-progress-ui .worktrees/-topos-p26-snapshot-progress-ui main
- Files changed: (worktree created)
- Result: Worktree ready at commit 48f2cbf.
- Follow-up: Implement app.py changes.

- Action: Replaced synchronous action_create_snapshot with async worker using Textual run_worker(thread=True).
  Added _snapshot_in_progress guard, _run_snapshot_worker, _on_snapshot_done, _on_snapshot_failed helpers.
- Commands: python3 -m py_compile topos/src/topos/ui/app.py
- Files changed: topos/src/topos/ui/app.py
- Result: Compiles cleanly. Status shows "snapshot running: <entity>" before work starts,
  "snapshot already running" on duplicate keypress, and success/failure on completion.

- Action: Discovered that Textual run_worker(thread=True) must receive a callable, not a called function.
  Fixed by storing snapshot context in instance attributes and passing self._run_snapshot_worker (no args).
- Files changed: topos/src/topos/ui/app.py
- Result: Worker correctly runs in background thread; call_from_thread safely updates UI.

- Action: Wrote 4 focused tests: running_status, duplicate_keypress_guard, success_reports_path,
  handled_exception_reports_failure.
- Commands: .venv-p26/bin/python -m pytest topos/tests/test_ui_app.py -k "snapshot" -v
- Result: All 6 snapshot tests pass (including 2 existing P15 tests). 181 suite-wide pass.

- Action: Updated docs: README.md P26→Done, ROADMAP.md P15/P26, STATUS.md snapshots/quality gate.
- Result: Docs reflect current state.

- Action: Wrote P26-LOG.md and P26-REPORT.md.
- Result: Handoff artifacts complete.

- Action: Committed feature branch.
- Result: Branch ready for review.

- Action: Controller reviewed, patched, merged P26 into `main`, and reran validation from the main checkout.
- Commands: `git merge --no-ff feat/topos-p26-snapshot-progress-ui -m "Merge topos P26 snapshot progress UI"`,
  `PYTHONPATH=topos/src .worktrees/-topos-p26-snapshot-progress-ui/.venv-p26/bin/python -m pytest topos/tests/test_ui_app.py -k "snapshot" -q`,
  `find topos/src -name '*.py' -print0 | xargs -0 .worktrees/-topos-p26-snapshot-progress-ui/.venv-p26/bin/python -m py_compile`,
  `PYTHONPATH=topos/src .worktrees/-topos-p26-snapshot-progress-ui/.venv-p26/bin/python -m pytest topos/tests -q`.
- Files changed: `topos/tests/test_ui_app.py`, `topos/docs/ROADMAP.md`,
  `topos/docs/STATUS.md`, `topos/handoff/reports/P26-LOG.md`,
  `topos/handoff/reports/P26-REPORT.md`.
- Result: P26 merged and validated on `main`: focused snapshot tests
  `6 passed, 17 deselected in 1.85s`; full suite `181 passed in 28.52s`;
  compile check clean.
```

## Decisions

- Decision: Use instance attributes (_snapshot_entity_key, _snapshot_frame, _snapshot_previous_frames)
  to pass context to the worker thread instead of closures.
  Reason: Textual's run_worker(thread=True) receives a callable. Closures over mutable state
  would capture app references; instance attributes are cleaner and follow existing patterns.
  Impact: Slight structural change but works reliably with call_from_thread.

- Decision: Use a boolean flag (_snapshot_in_progress) for duplicate-start guard rather than
  tracking worker references.
  Reason: Simpler, tests can verify the flag directly, and no need to manage worker lifecycle.
  Impact: Guard is easily testable and predictable.

- Decision: For the failure test, use RuntimeError (not OSError) from the injected provider.
  Reason: collect_systemctl_show catches OSError internally and returns a graceful status;
  RuntimeError propagates to the worker's except clause as intended.
  Impact: Failure test correctly exercises the snapshot worker failure path.

## Blockers

- None.

## Validation

```bash
.venv-p26/bin/python -m pytest topos/tests/test_ui_app.py -k "snapshot" -v
# 6 passed
.venv-p26/bin/python -m pytest topos/tests -q
# 181 passed in 28.78s
find topos/src -name '*.py' -print0 | xargs -0 .venv-p26/bin/python -m py_compile
# (no output)

PYTHONPATH=topos/src .worktrees/-topos-p26-snapshot-progress-ui/.venv-p26/bin/python -m pytest topos/tests -q
# 181 passed in 28.52s after merge to main
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
