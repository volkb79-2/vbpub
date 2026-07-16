# P9 Report

## What was built

- Added `topos/src/topos/damon/control.py` with fixture-safe controlled vaddr
  DAMON support:
  - free kdamond-slot selection that refuses busy/foreign sessions;
  - typed confirmation (`START`) before writes;
  - stale PID detection between planning and start;
  - topos-owned marker files under `$XDG_STATE_HOME/topos/damon/`;
  - audit logging to `actions.log`;
  - stop/teardown for owned sessions only.
- Extended `[damon]` config defaults:
  - `vaddr_sample_us = 100000`
  - `vaddr_aggr_us = 2000000`
  - `vaddr_update_us = 1000000`
  - `max_concurrent_targets = 4`
- Added `topos damon stop --all-mine` for owned-session cleanup.
- Added a drill-down DAMON control notice/hotkey surface that states root-only
  behavior, typed confirmation, and cleanup semantics without performing any
  live sysfs mutation from tests.
- Added P9 tests for start, stop, no-free-slot, non-root refusal, no-pids
  refusal, stale PID refusal, confirmation enforcement, CLI cleanup, audit log,
  and P8 passive ingestion of a topos-owned fixture session.

## Safety evidence

- No validation command wrote to live `/sys/kernel/mm/damon`; all mutating tests
  used `tmp_path` DAMON roots and fixture cgroup/proc trees.
- `damon/control.py` defaults to requiring root for start/stop.
- The CLI fixture bypass flag is hidden and used only by tests with explicit
  `--damon-root` and `--state-dir` temp paths.
- `stop_owned_sessions` only tears down sessions with a topos ownership marker
  and leaves foreign kdamond slots untouched.

## Deviations / gaps

- Real-host root start/stop acceptance was not executed. This session cannot
  safely mutate live DAMON state.
- The TUI side is intentionally minimal: it exposes the control notice/hotkey
  and confirmation requirement, while the fully interactive start/stop modal is
  deferred. The underlying control API and CLI cleanup path are implemented and
  covered.
- No `damon_stat` disable/restore path was implemented. That remains a future
  root-only control refinement; P9 will refuse when no free slot exists.

## Validation

```bash
PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p9-damon-control/topos/src python3 -m pytest topos/tests -q
# 68 passed in 10.32s
```

```bash
PYTHONPATH=/tmp/vbpub-topos-p9-damon-control/topos/src python3 -m py_compile $(find topos/src/topos -name '*.py' | sort)
# clean
```

```bash
PYTHONPATH=/tmp/vbpub-topos-p9-damon-control/topos/src python3 -m topos.cli --once --json --cgroup-root topos/tests/fixtures/cgroupfs/gstammtisch
# once 1 8
```

```bash
PYTHONPATH=/tmp/topos-pytest:/tmp/vbpub-topos-p9-damon-control/topos/src python3 -m topos.cli --replay topos/tests/fixtures/frames/gstammtisch-once.jsonl --step --ui-smoke
# ui smoke ok frames=1 view=tree profile=auto
```

## Known open items

- Add the full Textual typed-confirmation modal that calls `plan_start_session`
  and `start_planned_session` when running as root against a deliberate live
  DAMON root.
- Execute real-host root acceptance on a test container: start, verify P8
  columns populate, stop, and prove foreign sessions are untouched.
