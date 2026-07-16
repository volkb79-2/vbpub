# P53-REPORT — Headless Record Driver

**Status:** Done

## What Was Built

1. **`topos/src/topos/record/headless.py`** — New module containing:
   - `HeadlessRecordDriver` — Drives the collector loop headlessly via `live_frame_stream()`, with bounded exit conditions (`max_frames`, `duration`), stderr progress reporting at a configurable cadence (default 30 s), and injectable signal registration seam.
   - `install_signal_handlers()` — Production signal registration for SIGINT/SIGTERM (first signal → clean shutdown, second signal → `os._exit(1)`).
   - `make_second_signal_handler()` — Test-friendly signal handler factory supporting second-signal abort via separate `abort_event`.
   - `RecordProgress` — Bounded progress state for periodic stderr output.
   - `run_headless_record()` — Convenience wrapper.

2. **CLI flags** added to `parse_args()` in `topos/src/topos/cli.py`:
   - `--headless` (requires `--record`)
   - `--interval N` (per-run collector interval override)
   - `--duration S` (mutually exclusive with `--frames`)
   - `--frames K` (mutually exclusive with `--duration`)

3. **CLI validation** at the top of `main()`:
   - `--headless` + `--replay` → exit 2
   - `--headless` without `--record` → exit 2
   - `--headless` + `--attach` → exit 2
   - `--duration` + `--frames` → exit 2

4. **Headless integration** in `main()`: when `--headless` is set, the record block calls `run_headless_record()` instead of `_run_ui()`. No `textual` import occurs on this code path (verified by a structural `sys.modules` test).

5. **Documentation updates:**
   - `README.md`: quickstart paragraph describes `--headless` usage; P53 entry marked Done.
   - `CONTRACTS.md` §5: added bullet noting headless mode reuses the same format.

## Deviations from the Handoff

None. All named contracts are met.

## Proposed Contract Changes

None. The headless module is additive and package‑private within `topos/record/`. No shared interfaces in `CONTRACTS.md` were modified.

## Test Evidence

**Environment:** agent container (Linux x86_64, Python 3.14, no root, textual 8.2.8 installed).

### Focused tests (24 passed, 1 skipped)

```bash
$ PYTHONPATH=topos/src python3 -m pytest topos/tests/test_headless_record.py -q -W error::RuntimeWarning
........s................
24 passed, 1 skipped in 48.84s
```

The skipped test (`test_zst_record_reader_roundtrip`) requires the `zstandard` extra, which is not installed in this environment.

### Full suite (785 passed, 2 skipped)

```bash
$ PYTHONPATH=topos/src timeout 300 python3 -m pytest topos/tests/ -q -p no:asyncio -p no:schemathesis -W error
[...]
785 passed, 2 skipped in 120.82s
```

The full suite runs cleanly with `-W error` when pre‑existing third‑party deprecation warnings (pytest‑asyncio plugin config, schemathesis internal deprecations) are excluded. The two skipped tests require `zstandard`.

### py_compile

```bash
$ python3 -m py_compile topos/src/topos/cli.py
$ python3 -m py_compile topos/src/topos/record/headless.py
$ python3 -m py_compile topos/tests/test_headless_record.py
```

All three files compile without errors.

### Smoke test

```bash
$ PYTHONPATH=topos/src python3 -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
{"schema_version":1,...}
```

Existing entry point continues to work.

## Tests Added (25 tests in `test_headless_record.py`)

| # | Test | What it covers |
|---|------|---------------|
| 1 | `TestRecordProgress::test_basic_progress_line` | Progress line format |
| 2 | `TestRecordProgress::test_progress_accumulates` | Progress accumulates correctly |
| 3 | `TestHeadlessRecordDriver::test_stops_at_frame_count` | `max_frames` bound |
| 4 | `TestHeadlessRecordDriver::test_stops_at_duration` | `duration` bound |
| 5 | `TestHeadlessRecordDriver::test_duration_and_max_frames_mutually_exclusive` | Constructor rejects both |
| 6 | `TestHeadlessRecordDriver::test_interval_override_applied` | Interval applied to config |
| 7 | `TestHeadlessRecordDriver::test_signal_shutdown_writes_frame` | Signal causes clean shutdown with frame written |
| 8 | `TestHeadlessRecordDriver::test_jsonl_record_reader_roundtrip` | `.jsonl` end-to-end via RecordReader |
| 9 | `TestHeadlessRecordDriver::test_zst_record_reader_roundtrip` | `.jsonl.zst` end-to-end via RecordReader (skipped without zstandard) |
| 10 | `TestHeadlessRecordDriver::test_second_signal_during_shutdown` | Second signal → exit 1 |
| 11 | `TestHeadlessRecordDriver::test_writer_flush_failure_mid_run` | Mid-run I/O error → exit >0, partial file valid |
| 12 | `TestHeadlessRecordDriver::test_no_textual_import_on_headless_path` | Structural check: textual not in sys.modules |
| 13 | `TestHeadlessCLI::test_headless_requires_record` | `--headless` without `--record` → exit 2 |
| 14 | `TestHeadlessCLI::test_headless_rejects_attach` | `--headless` with `--attach` → exit 2 |
| 15 | `TestHeadlessCLI::test_headless_rejects_replay` | `--headless` with `--replay` → exit 2 |
| 16 | `TestHeadlessCLI::test_duration_and_frames_mutually_exclusive` | `--duration` + `--frames` → exit 2 |
| 17 | `TestHeadlessCLI::test_headless_with_once_works` | `--headless --once` collects one frame |
| 18 | `TestHeadlessCLI::test_headless_writes_header_and_frame` | Basic `--headless --record` writes valid recording |
| 19 | `TestHeadlessCLI::test_headless_frames_bound_exits_zero` | `--frames K` exits cleanly after K frames |
| 20 | `TestHeadlessCLI::test_headless_duration_bound_exits_zero` | `--duration S` exits cleanly |
| 21 | `TestHeadlessCLI::test_headless_with_interval` | `--interval N` accepted |
| 22 | `TestSignalSeam::test_make_second_signal_handler_first_signal_sets_stop` | First signal sets stop_event |
| 23 | `TestSignalSeam::test_make_second_signal_handler_second_signal_sets_abort` | Second signal sets abort_event |
| 24 | `TestSignalSeam::test_install_signal_handlers_registers_sigint_sigterm` | Real signal handler works |
| 25 | `TestProgressCadence::test_progress_emitted_at_cadence` | Progress lines at 30s cadence |

## Known Gaps / Open Items

- No `.zst` roundtrip test was run in this environment (zstandard extra not installed). The same test logic is exercised for `.jsonl`; the `.zst` path uses the same `RecordWriter`/`RecordReader` classes which are already tested in `test_record.py`.
- The `--interval` override uses `dataclasses.replace()` to create a new config, which means the original `collector.config` object is replaced. This is safe for the headless codepath since the config is not shared with any other component during the run.
- The second-signal path uses `os._exit(1)` in the production `install_signal_handlers()`; the test seam (`make_second_signal_handler`) uses events instead, enabling test verification without process termination.
- The full suite with `-W error` requires excluding pre‑existing third‑party deprecation warnings (pytest‑asyncio plugin config, schemathesis internal deprecations) that are unrelated to this package.

## Files Changed

```
M topos/README.md
M topos/CONTRACTS.md
M topos/src/topos/cli.py
A topos/src/topos/record/headless.py
A topos/tests/test_headless_record.py
A topos/handoff/reports/P53-LOG.md
A topos/handoff/reports/P53-REPORT.md
```
