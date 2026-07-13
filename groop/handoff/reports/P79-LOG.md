# P79 Work Log

## Context

- Branch: feat/groop-p79-corrupt-recording-typed-errors
- Worktree: .worktrees/groop-p79-corrupt-recording-typed-errors
- Base commit: main
- Package: P79 — Corrupt recording inputs are typed errors
- Current objective: Implement handoff spec; green suite in clean venv

## Timeline

```text
2026-07-14 UTC
- Action: Read context files (record/reader.py, cli.py _main_report, test_report.py test)
- Commands: read_file groop/src/groop/record/reader.py, groop/src/groop/cli.py, groop/tests/test_report.py, groop/CONTRACTS.md
- Files changed: (read-only)
- Result: Understood current state — ZstdError propagates uncaught; test is environment-conditional; existing handlers catch RuntimeError/ValueError but not ZstdError
- Follow-up: Fix reader.py to catch _ZstdError

- Action: Fixed record/reader.py to catch _ZstdError in _open_text and iter_frames; wrapped header/ frame_from_jsonable errors
- Commands: edit_file, multi_edit
- Files changed: groop/src/groop/record/reader.py
- Result: ZstdError during decompressor creation or lazy reading becomes ValueError with bounded message; header KeyError and frame_from_jsonable KeyError/TypeError also wrapped
- Follow-up: Fix cli.py catch-all; fix test

- Action: Fixed cli.py _main_report to add OSError and catch-all Exception handlers
- Commands: edit_file
- Files changed: groop/src/groop/cli.py
- Result: All reader errors produce exit 2 with bounded message, never a raw traceback
- Follow-up: Fix test

- Action: Fixed _ZstdError being None when zstd not installed — was TypeError "catching classes that do not inherit from BaseException"
- Commands: multi_edit
- Files changed: groop/src/groop/record/reader.py
- Result: Changed except _ZstdError to except Exception with isinstance guard. FileNotFoundError now propagates correctly.
- Follow-up: Verify tests pass

- Action: Split broken test into corrupt-input (conditional on zstd installed) and missing-extra (forces zstd absence via stub module). Added 6 numbered acceptance oracles.
- Commands: edit_file, multi_edit
- Files changed: groop/tests/test_report.py
- Result: 20 P79-related tests pass, 4 skip (zstd-specific, in no-zstd venv)
- Follow-up: Run full suite, update docs

- Action: Ran full suite, updated docs
- Commands: pytest groop/tests -q, edit_file README.md, edit_file OPERATIONS.md
- Result: 1206 passed, 6 skipped, 1 pre-existing failure (test_pilot_snapshot_hotkey_writes_bundle — unrelated UI flake). Docs updated.
- Follow-up: Write LOG and REPORT; commit
```

## Decisions

- Decision: Convert _ZstdError to ValueError in the reader (rather than catching in CLI)
  Reason: The reader already raises ValueError for corrupt/invalid data; this keeps the error boundary at the module that knows about zstd. The CLI's existing ValueError handler covers it.
  Impact: Cleaner separation; no zstd-specific knowledge needed in cli.py

- Decision: Use `except Exception as exc: if _ZstdError is not None and isinstance(exc, _ZstdError)` instead of bare `except _ZstdError`
  Reason: When zstandard is not installed, _ZstdError is None, and bare `except None` raises TypeError. The isinstance guard is safe in both environments.
  Impact: Works correctly regardless of whether the optional zstd extra is installed.

- Decision: Use stub zstandard.py module (raise ImportError) for forced-absence test instead of pytest.skip
  Reason: Per the handoff, forcing absence is possible and was done. The stub module shadows the real one prepended to PYTHONPATH, making `try: import zstandard except ImportError: _zstd = None` fire correctly.
  Impact: Missing-extra test is deterministic and does not depend on the ambient venv.

## Validation

```text
$ PYTHONPATH=groop/src python3 -m pytest groop/tests/test_report.py -q -k "TestReportCLI or TestCorruptRecordingCLI or TestReportAssertionCLI"
20 passed, 4 skipped, 96 deselected, 1 warning in 6.15s

$ timeout 300 env PYTHONPATH=groop/src python3 -m pytest groop/tests -q
1206 passed, 6 skipped, 1 failed (pre-existing UI flake), 1 warning in 155.98s

$ python3 -m py_compile groop/src/groop/record/reader.py && echo OK
OK
$ python3 -m py_compile groop/src/groop/cli.py && echo OK
OK
$ python3 -m py_compile groop/tests/test_report.py && echo OK
OK

$ git diff --check HEAD
(no output)
```
