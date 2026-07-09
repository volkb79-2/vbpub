# P35 — Acceptance Steady Harness

## What Was Built

Extended the P33 `groop.acceptance` module with a `steady` subcommand that
runs a repeatable collector loop — rootless, no Textual, no subprocesses —
to produce CPU/RSS evidence for `TUI-SPEC.md` §9 items 1 and 2.

```bash
python -m groop.acceptance steady [--cgroup-root PATH] [--samples N]
    [--interval-s SECONDS] [--max-cpu-pct FLOAT] [--max-rss-kb INT]
    [--json] [--pretty-json]
```

### Changes to `groop/src/groop/acceptance.py`

- **`SteadySample`** dataclass — per-sample record (index, wall_s, entity_count)
- **`SteadyResult`** dataclass — ok, version, python, platform, samples
  requested/completed, measurements (wall_s, user_s, sys_s, rss_kb,
  avg_sample_wall_s, cpu_pct), entity_counts (min/max/last), threshold_errors
- **`run_steady()`** — core loop: collects `samples` frames with `interval_s`
  between them; accepts injectable `_sleep` and `_perf_counter` for deterministic
  testing; measures wall/user/sys/RSS via `resource.getrusage(RUSAGE_SELF)`;
  computes CPU percent; applies optional `--max-cpu-pct` and `--max-rss-kb`
  thresholds
- **`format_steady_text()`** / **`format_steady_json()`** — output formatters
  with deterministic JSON (sorted keys, compact) and concise text
- **`acceptance_main()`** — unified entry point dispatching to `smoke` or
  `steady` via `build_parser()`; validates args (samples>0, interval>=0);
  exit 0/1/2
- **`steady` subcommand** in `build_parser()` — all CLI args with defaults

### Tests: `groop/tests/test_acceptance.py`

11 new tests (7 unit + 4 subprocess) covering:
- JSON/text fixture steady with `--samples 2 --interval-s 0`
- CPU threshold failure (exit 1)
- RSS threshold failure (exit 1)
- Injectable sleep/perf_counter for deterministic testing
- Pretty JSON parseable and deterministically sorted
- Invalid samples/interval values (exit 2)
- Subprocess steady JSON/pretty-json/threshold/invalid-args
- All 13 existing smoke tests still pass (24 total)

### Documentation updated

- `groop/MEASUREMENTS.md` — added P35 steady section as the preferred
  rootless collector evidence path before live TUI acceptance
- `groop/docs/OPERATIONS.md` — added steady command example

## Deviations from the Handoff

None. The handoff was followed exactly.

- Reused the existing P33 module design as specified.
- Preserved `smoke` command behavior completely.
- Used `resource.getrusage(RUSAGE_SELF)` as P33 does.
- `SteadyResult` is a separate dataclass (clean separation from SmokeResult).

## Proposed Contract Changes

None. P35 is additive and package-private. No shared interfaces were touched.

## Test Evidence

### Acceptance tests (24 total: 13 smoke + 11 steady)

```bash
PYTHONPATH=groop/src /home/vb/volkb79-2/vbpub/.venv/bin/python -m pytest groop/tests/test_acceptance.py -v
# 24 passed in 4.49s
```

### Full non-UI suite (includes P35 tests)

```bash
PYTHONPATH=groop/src /home/vb/volkb79-2/vbpub/.venv/bin/python -m pytest groop/tests -q \
  --ignore=... (7 UI test files with pre-existing Textual version incompatibility)
# 226 passed in 31.21s
```

### py_compile

```bash
PYTHONPATH=groop/src python3 -m py_compile groop/src/groop/acceptance.py groop/tests/test_acceptance.py
# exit=0
```

## Known Gaps / Open Items

- The harness collects host metrics from real `/proc` (same design choice as
  P33). Fixture-based runs use `--cgroup-root` for deterministic cgroup data
  but still read the local host's `/proc/stat`, `/proc/meminfo`, etc.
  This is acceptable: steady-state evidence is intended for a real host, and
  fixture runs are primarily for regression/determinism.
- UI tests (`test_ui_app.py`) have 16 pre-existing failures from a newer
  Textual version (removed `.renderable` attribute). Same as P33; unrelated.
