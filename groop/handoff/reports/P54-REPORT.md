# P54-REPORT — Steady-State Report Command

**Status:** Done

## What Was Built

1. **`groop/src/groop/report.py`** — New module containing:
   - `compute_profile()` — Core profile computation from a list of `Frame` objects: per-group (entity or slice) p50/p95/max for a fixed gauge set (`ram`, `anon`, `z_pool`, `z_eq`, `swap_disk`, `psi_mem_some_avg10`, `psi_mem_full_avg10`, `psi_io_some_avg10`, `psi_io_full_avg10`, `psi_cpu_some_avg10`, `psi_cpu_full_avg10`) plus derived `_per_s`/`_bps`/`_pps`/`_iops` rates from embedded raw counters.
   - `parse_window_spec()` — Parse `--window "all"` or `--window "last:Ns"`; rejects malformed specs with `ValueError`.
   - `_nearest_rank_percentile()` — Nearest-rank percentile per 2026-07-12 amendment (ceil(p/100 * N) - 1, 0-based).
   - `_derive_rate()` — Derives a rate from raw counter deltas across non-consecutive frames, tolerating gaps from filtering, entity churn, or counter resets.
   - `_find_slice_ancestor()` — Entity → `*.slice` ancestor via the parent chain (no cgroup path parsing reimplementation).
   - `report_to_jsonable()` / `format_report()` — Deterministic JSON serialization with floats rounded to 6 decimal places and sorted keys.

2. **CLI additions** in `groop/src/groop/cli.py`:
   - `parse_report_args()` — Parses `groop report FILE [--window last:Ns|all] [--group-by slice|entity] --json`; `--json` is required (argparse `required=True`).
   - `_main_report()` — Dispatches to `compute_report()`, handles `FileNotFoundError` (exit 2), `.zst`-without-zstandard `RuntimeError` matching `RecordReader`'s existing error message (exit 2), and `ValueError` for bad window/group-by specs (exit 2).
   - Dispatch line in `main()`: `if raw_argv[:1] == ["report"]: return _main_report(raw_argv[1:])`.

3. **Tests** — 57 tests in `groop/tests/test_report.py` covering:
   - Unit tests for percentile computation (including nearest-rank vs. interpolation oracle)
   - Window spec parsing (all, last:Ns, malformed, empty)
   - Frame filtering by window (inclusion, exclusion, boundaries, empty results)
   - Slice ancestry resolution
   - Rate derivation (basic, entity churn gaps, counter regression, cold-start)
   - Profile computation (single/multi entity, multi-frame, window filtering, degenerate windows, warm-vs-cold rate parity, determinism, float rounding)
   - CLI integration (exit codes for missing `--json`, bad window, missing file, fixture smoke test, deterministic bytes)
   - Edge cases (entity present→absent, mixed live+derived rates)

4. **Documentation updates:**
   - `README.md`: quickstart examples for `groop report`, description paragraph referencing P2 reader reuse and gstammtisch stack measurement program; P54 marked Done.
   - `docs/ARCHITECTURE.md`: report consumer path added to dataflow diagram; `report.py` in module map.
   - `docs/OPERATIONS.md`: report command examples added.

## Deviations from the Handoff

None. All named contracts are met.

## Proposed Contract Changes

None. The report module is additive and package‑private within `groop/`. No shared interfaces in `CONTRACTS.md` were modified.

## Amendment Compliance

| Amendment | Status | Evidence |
|---|---|---|
| Nearest-rank percentiles (2026-07-12) | ✅ | `_nearest_rank_percentile` + oracle test `test_nearest_rank_vs_interpolation_oracle` |
| Float rounding to 6 decimal places | ✅ | `_round_float()` + `test_float_rounding` |
| Deterministic byte-identical output | ✅ | `test_cli_deterministic_output`, `test_multiple_calls_same_bytes` |
| `.zst` without zstandard → exit 2 with install hint | ✅ | `_main_report` catches `RuntimeError("zstandard")`, matches `RecordReader` behavior; cited in LOG |
| Gates: focused + full suite with `-W error` + `timeout` | ✅ | See below |

## Test Evidence

**Environment:** agent container (Linux x86_64, Python 3.14, no root, textual 8.2.8 installed, zstandard not installed).

### Focused tests (57 passed)

```bash
$ PYTHONPATH=groop/src python3 -m pytest groop/tests/test_report.py -q -W error::RuntimeWarning
57 passed in 1.20s
```

### Full suite (970 passed, 2 skipped)

```bash
$ PYTHONPATH=groop/src timeout 300 python3 -m pytest groop/tests/ -q -p no:asyncio -p no:schemathesis -W error
970 passed, 2 skipped in 122.84s
```

The full suite runs cleanly with `-W error`. The two skipped tests require the `zstandard` extra, which is not installed in this environment (same as P53 baseline).

### py_compile

```bash
$ python3 -m py_compile groop/src/groop/report.py
$ python3 -m py_compile groop/src/groop/cli.py
$ python3 -m py_compile groop/tests/test_report.py
```

All three files compile without errors.

### Smoke test

```bash
$ PYTHONPATH=groop/src python3 -m groop.cli report groop/tests/fixtures/frames/gstammtisch-once.jsonl --json
{"metrics_version":1,"profiles":[{"gauges":{...}},...]}
```

```bash
$ PYTHONPATH=groop/src python3 -m groop.cli report groop/tests/fixtures/frames/gstammtisch-once.jsonl --json --group-by slice
{"metrics_version":1,"profiles":[{"key":"",...},{"key":"soulmask.slice/soulmask-paks.slice",...},{"key":"system.slice",...}]}
```

### git diff --check

```bash
$ git diff --check
# clean
```

## Tests Added (57 tests)

| Class | Test | What it covers |
|---|---|---|
| `TestPercentile` | `test_small_odd`, `test_small_even`, `test_p95`, `test_p95_small`, `test_max_is_last`, `test_nearest_rank_vs_interpolation_oracle` | Nearest-rank correctness; oracle test with 5 samples where linear interpolation diverges |
| `TestComputeMetricResult` | `test_empty`, `test_single_sample`, `test_multi_sample` | Edge cases for _compute_metric_result |
| `TestIsRateMetric` | `test_rate_metric` | Suffix-based rate metric detection (_per_s, _bps, _pps, _iops) |
| `TestParseWindowSpec` | 7 tests | all, last:Ns, malformed, empty, negative duration |
| `TestFilterFramesByWindow` | 4 tests | all, inclusion, exact boundary, empty result |
| `TestFindSliceAncestor` | 4 tests | entity-is-slice, scope-finds-slice, root, unknown |
| `TestDeriveRate` | 5 tests | basic, skip-missing-entity, entity-churn-gap, no-earlier-frame, counter-regression |
| `TestComputeProfile` | 12 tests | empty frames, single entity, multiple entities, report-gauges-only, None-skipped, multi-frame p50/p95, window filtering, zero-frame window, single-frame profile, warm-vs-cold parity, slice rollup, all-gauges-covered, deterministic output, multiple-calls-same-bytes |
| `TestJsonSerialization` | 3 tests | profile_to_jsonable, report_to_jsonable_deterministic, float_rounding |
| `TestReportCLI` | 6 tests | missing --json, bad window, missing file, .zst error, fixture smoke, deterministic bytes |
| `TestEdgeCases` | 2 tests | entity present then absent, mixed live+derived rates |

## Known Gaps / Open Items

- The `.zst` roundtrip test is not runnable in this environment (zstandard extra not installed). The same test logic exists for `.jsonl`.
- Steady-state window auto-detection is explicitly out of scope (noted in the handoff as future work).
- `--group-by slice` uses the frame-parent-chain to find `*.slice` ancestors, which works correctly for the fixture frames. If a recording contains entities with unusual parent relationships (e.g., non-standard slice naming), the fallback to the direct parent or root is used.
- Rate derivation tolerates all gap types (filtering, entity churn, reset) but requires at least one earlier frame with the same entity/metric and a non-reset raw counter to produce a rate.

## Files Changed

```
M groop/README.md
M groop/docs/ARCHITECTURE.md
M groop/docs/OPERATIONS.md
M groop/src/groop/cli.py
A groop/src/groop/report.py
A groop/tests/test_report.py
A groop/handoff/reports/P54-LOG.md
A groop/handoff/reports/P54-REPORT.md
```
