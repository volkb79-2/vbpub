# P33 - Release Smoke Harness

## What Was Built

A rootless, deterministic acceptance smoke harness for release-confidence
evidence, runnable as:

```bash
python -m groop.acceptance smoke [--cgroup-root PATH] [--replay PATH] [--json] [--pretty-json]
```

### Module: `groop/src/groop/acceptance.py`

- **`run_smoke()`** - core logic: collects one frame via `Collector()`, runs
  `frame_to_jsonable`/`frame_from_jsonable` round-trip, counts metric source
  labels, optionally loads a recording via `ReplayDriver.from_path()`.
- **Measurements** - wall time, user CPU, sys CPU, max RSS via
  `time.perf_counter()` + `resource.getrusage(RUSAGE_SELF)`.
- **Output** - deterministic JSON or concise text; exit code 0 (all pass),
  1 (check failures), 2 (usage error).
- **No Textual import** - uses only stdlib + groop packages below `ui/`.
- **No subprocess execution** - reads cgroup files and /proc only; writes
  nothing but stdout/stderr.

### Checks performed

| Check | Description |
|---|---|
| `collect` | Create `Collector`, call `collect_once()`, capture jsonable frame |
| `serialize` | `frame_to_jsonable` + `frame_from_jsonable` round-trip |
| `source_labels` | Count `MetricValue.src` distribution across host + entity metrics |
| `replay` | (optional) Load recording with `ReplayDriver.from_path()`, report frame count/timestamps |

### Tests: `groop/tests/test_acceptance.py`

13 tests (6 unit + 7 subprocess):

- JSON/text fixture smoke (with `run_smoke` and subprocess)
- Replay summary with `gstammtisch-once.jsonl`
- Non-existent replay path returns exit 1
- Pretty JSON parseable and indented
- No Textual import (both unit and subprocess)
- Missing subcommand exits 2

### Documentation updated

- `groop/MEASUREMENTS.md` - added P33 smoke harness as the preferred rootless
  evidence path, with example commands and output.
- `groop/docs/OPERATIONS.md` - added release-smoke command example.

## Deviations from the Handoff

None. The handoff was followed exactly.

- Single-file `groop/src/groop/acceptance.py` with `if __name__ == "__main__"`
  entry point, as specified.
- No changes to `cli.py` - harness is independent.
- No Textual import, no subprocess execution, no host mutation.

## Proposed Contract Changes

None. P33 is additive and package-private. No shared interfaces were touched.

## Test Evidence

### Acceptance tests (P33)

```bash
PYTHONPATH=groop/src /home/vb/volkb79-2/vbpub/.venv/bin/python -m pytest groop/tests/test_acceptance.py -v
# 13 passed in 1.80s
```

### Full non-UI suite (agent environment)

```bash
PYTHONPATH=groop/src /home/vb/volkb79-2/vbpub/.venv/bin/python -m pytest groop/tests/ -q \
  --ignore=... (7 UI test files with pre-existing Textual version incompatibility)
# 204 passed in 24.82s
```

### py_compile

```bash
PYTHONPATH=groop/src python3 -m py_compile groop/src/groop/acceptance.py groop/tests/test_acceptance.py
# exit=0
```

### Controller review validation

```bash
PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest \
  groop/tests/test_acceptance.py \
  groop/tests/test_collector.py \
  groop/tests/test_record.py -q
# 34 passed in 10.04s

PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m py_compile \
  groop/src/groop/acceptance.py \
  groop/tests/test_acceptance.py
# clean, exit 0

PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q
# 292 passed in 33.03s

# Post-merge main validation
PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m pytest groop/tests -q
# 303 passed in 37.10s

PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m py_compile \
  groop/src/groop/daemon/status.py \
  groop/src/groop/acceptance.py \
  groop/src/groop/cli.py \
  groop/tests/test_daemon_status.py \
  groop/tests/test_acceptance.py
# clean, exit 0

PYTHONPATH=groop/src /tmp/p25-venv/bin/python -m groop.acceptance smoke \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --json
# exit 0, ok true, 8 entities, 572 metric source labels, wall 0.1794s, RSS 89256 KB
```

### Live smoke with fixture

```bash
PYTHONPATH=groop/src python3 -m groop.acceptance smoke \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --replay groop/tests/fixtures/frames/gstammtisch-once.jsonl \
  --json
# {"ok": true, ...} exit=0

# Non-existent replay:
PYTHONPATH=groop/src python3 -m groop.acceptance smoke \
  --cgroup-root groop/tests/fixtures/cgroupfs/gstammtisch \
  --replay /nonexistent/path.jsonl
# exit=1 (replay check fails as expected)
```

## Known Gaps / Open Items

- The harness collects host metrics from real `/proc` (default `Collector`
  settings), so fixture-based runs still read the local host's `/proc/stat`,
  `/proc/meminfo`, etc. This is acceptable: the smoke is for release
  confidence on a real host, and tests only need a fixture cgroup root to be
  deterministic.
- The agent environment had a local Textual API mismatch for UI tests. The
  controller reran the full suite in the project review environment and it
  passed.
