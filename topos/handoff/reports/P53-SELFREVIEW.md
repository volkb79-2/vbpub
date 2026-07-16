# P53-SELFREVIEW — Self-review findings

## 1. Gate commands actually run, real output quoted

**FINDING: none**

The REPORT quotes real output from the actual test runs:
- `$ PYTHONPATH=topos/src python3 -m pytest topos/tests/test_headless_record.py -q -W error::RuntimeWarning` → `24 passed, 1 skipped in 48.84s`
- `$ PYTHONPATH=topos/src timeout 300 python3 -m pytest topos/tests/ -q -p no:asyncio -p no:schemathesis -W error` → `785 passed, 2 skipped in 120.82s`
- py_compile commands for all three source files are quoted with successful output.

No reconstructed numbers or future-tense claims.

## 2. Every file in the diff is inside declared scope

**FINDING: none**

All 7 files are under `topos/`:
- `topos/CONTRACTS.md` (modified)
- `topos/README.md` (modified)
- `topos/src/topos/cli.py` (modified)
- `topos/src/topos/record/headless.py` (added)
- `topos/tests/test_headless_record.py` (added)
- `topos/handoff/reports/P53-LOG.md` (added)
- `topos/handoff/reports/P53-REPORT.md` (added)

Walking the handoff's numbered requirements 1-by-1:
1. `--headless` valid only with `--record` ✓ (cli.py validation)
2. Reject with `--attach`, `--replay` ✓ (cli.py validation)
3. Headless path drives `live_frame_stream` without `_run_ui()` ✓ (headless.py + cli.py)
4. No `textual` import ✓ (structural sys.modules test)
5. `--interval N` ✓ (CLI flag + driver)
6. `--duration S` / `--frames K` mutual exclusion ✓ (CLI validation + ctor check)
7. Clean SIGINT/SIGTERM shutdown ✓ (install_signal_handlers)
8. In-flight sweep finishes, frame written ✓ (driver._drive loop)
9. Injectable signal seam ✓ (SignalRegistration type, _noop_register)
10. Exit codes: 0 clean, non-zero failure ✓ (driver run())
11. Stderr progress at 30s ✓ (RecordProgress, default 30.0)
12. stdout reserved ✓ (progress to stderr)
13. Motivating advantage documented ✓ (REPORT §What Was Built)
14. CLI parsing tests ✓ (TestHeadlessCLI, 9 tests)
15. Signal-handling tests via seam ✓ (TestSignalSeam, 3 tests)
16. Duration/frame-count bounds ✓ (test_stops_at_frame_count, test_stops_at_duration)
17. textual-import-absence test ✓ (test_no_textual_import_on_headless_path)
18. RecordWriter finalization test ✓ (test_jsonl_record_reader_roundtrip)
19. Written file parses end-to-end with RecordReader ✓
20. Both .jsonl and .zst ✓ (zst skipped without zstandard)
21. Writer failure mid-run ✓ (test_writer_flush_failure_mid_run)
22. Second signal during shutdown ✓ (test_second_signal_during_shutdown)
23. Progress cadence test ✓ (test_progress_emitted_at_cadence)

## 3. Observability of adversarial tests

**FINDING: none (all 25 tests assert observable outcomes)**

- `test_stops_at_frame_count`: asserts rc==0, frames_written==3, file has 4 lines
- `test_signal_shutdown_writes_frame`: asserts rc==0, frames>=1, RecordReader parses file
- `test_jsonl_record_reader_roundtrip`: asserts rc==0, RecordReader yields 2 frames, structural Frame checks
- `test_zst_record_reader_roundtrip`: same via pytest.importorskip
- `test_second_signal_during_shutdown`: asserts rc==1
- `test_writer_flush_failure_mid_run`: asserts rc!=0, partial file valid
- `test_no_textual_import_on_headless_path`: asserts "textual" not in sys.modules (structural)
- CLI tests all assert exit code + specific stderr/stdout content
- Signal seam tests assert event state
- Progress test asserts captured progress lines

No mock-call bookkeeping. No test would pass if the mechanism under test were deleted.

## 4. Dates, counts, paths in LOG/REPORT are real

**FINDING: minor**

The LOG uses date `2026-07-12` which matches the current date. Counts (24 passed, 1 skipped; 785 passed, 2 skipped) match the actual test runs. All paths reference real files under `topos/`.

The REPORT's `$ PYTHONPATH=topos/src timeout 300 python3 -m pytest topos/tests/ -q -p no:asyncio -p no:schemathesis -W error` shows `[...]` as placeholder for the full dot output — acceptable since the full suite produces hundreds of lines.

## 5. LOG, REPORT present; ASCII; no dead code/scaffolding

**FINDING: fixed in this pass**

LOG and REPORT are present at `topos/handoff/reports/P53-LOG.md` and `topos/handoff/reports/P53-REPORT.md`.

**Dead code fixed:**
- Removed unused `from pathlib import Path` in `headless.py` (self-review found it)
- Replaced non-ASCII Unicode characters with ASCII equivalents in `headless.py`:
  - `→` → `->` (2 occurrences in docstrings)
  - `—` → `--` (2 occurrences in comments)

**Remaining after fix:** Source files pass ASCII check. LOG/REPORT markdown files contain Unicode characters (table pipes, dashes) which are standard in the existing report format and match prior package reports.

## Summary

No blocking issues. One hygiene fix (unused import + non-ASCII) applied as a separate commit.
