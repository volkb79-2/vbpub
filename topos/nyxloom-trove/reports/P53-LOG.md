# P53 Work Log — Headless Record Driver

## Context

- Branch: `feat/topos-p53-headless-record-driver`
- Worktree: `.worktrees/topos-p53-headless-record-driver`
- Base commit: main (11 ahead of origin/main)
- Package: P53 — Headless record driver
- Current objective: implement `--headless` record mode

## Timeline

```text
2026-07-12 23:03 UTC
- Action: Explored codebase (cli.py, record module, tests, CONTRACTS.md)
- Files: topos/src/topos/cli.py, topos/src/topos/record/live.py, topos/src/topos/record/writer.py, topos/tests/
- Result: Understood existing --record path, live_frame_stream, RecordWriter interfaces
- Follow-up: Create headless driver module

2026-07-12 23:05 UTC
- Action: Created topos/src/topos/record/headless.py with HeadlessRecordDriver
- Files: topos/src/topos/record/headless.py
- Result: Module with HeadlessRecordDriver, signal helpers (install_signal_handlers, make_second_signal_handler), RecordProgress, and run_headless_record convenience wrapper
- Follow-up: Add CLI flags to parse_args

2026-07-12 23:06 UTC
- Action: Added --headless, --interval, --duration, --frames flags to parse_args()
- Files: topos/src/topos/cli.py
- Decision: Validation order: headless+replay checked before headless without record, to ensure correct error messages
- Follow-up: Integrate headless path in main() record block

2026-07-12 23:07 UTC
- Action: Integrated headless path in main(); added flag validation
- Files: topos/src/topos/cli.py
- Decision: --headless --once behaves like --record --once (single frame via live_frame_stream, not the headless loop)
- Follow-up: Write tests

2026-07-12 23:09 UTC
- Action: Wrote test_headless_record.py with 25 tests
- Files: topos/tests/test_headless_record.py
- Result: Tests cover CLI validation, driver unit tests, signal seam tests, progress cadence
- Follow-up: Fix test failures

2026-07-12 23:11 UTC
- Action: Fixed frozen interval issue (dataclasses.replace), validation order, flaky writer test, monotonic clock injection to live_frame_stream
- Files: topos/src/topos/record/headless.py, topos/tests/test_headless_record.py, topos/src/topos/cli.py
- Result: 24 tests pass (1 skipped for zstandard), all gates passed
- Follow-up: Update docs and run full suite

2026-07-12 23:20 UTC
- Action: Updated README.md and CONTRACTS.md docs
- Files: topos/README.md, topos/CONTRACTS.md
- Result: Quickstart updated, work package entry marked Done, recording contract updated
- Follow-up: Run full test suite

2026-07-12 23:22 UTC
- Action: Ran full test suite (785 passed, 2 skipped)
- Result: Full suite green with appropriate warning filters
- Follow-up: Write reports, commit
```

## Decisions

- Decision: Interval override uses dataclasses.replace
  Reason: ToposConfig is frozen; assignment to .interval raises FrozenInstanceError
  Impact: The driver creates a new config instance rather than mutating the original

- Decision: Signal seam injectable via HeadlessRecordDriver parameter
  Reason: Test seam must not surface as CLI flag (standing contract in CONTRACTS.md)
  Impact: install_signal_handlers is the default; tests use _noop_register

- Decision: --headless --once behaves like --record --once (single frame, not headless loop)
  Reason: --once semantics are "collect one frame and exit" regardless of --headless
  Impact: --headless --once does not need --frames or --duration

## Validation

```bash
# Focused tests
PYTHONPATH=topos/src python3 -m pytest topos/tests/test_headless_record.py -q -W error::RuntimeWarning
# 24 passed, 1 skipped

# Full suite
PYTHONPATH=topos/src timeout 300 python3 -m pytest topos/tests/ -q -p no:asyncio -p no:schemathesis -W error
# 785 passed, 2 skipped

# py_compile on changed files
python3 -m py_compile topos/src/topos/cli.py
python3 -m py_compile topos/src/topos/record/headless.py
python3 -m py_compile topos/tests/test_headless_record.py
```

## Handoff Checklist

- [x] Report file written.
- [x] Log file current.
- [x] Tests/compile/smoke recorded.
- [x] Known gaps documented.
- [x] Feature branch committed.
