# P44 Report - Daemon-Owned paddr Lifecycle

**Branch:** `feat/groop-p44-daemon-paddr-lifecycle`
**Base:** `9d6327b` (docs(groop): carve P44-P46 v2 safety slices)
**Date:** 2026-07-10

## What Was Built

### `groop/src/groop/config.py` — `DamonConfig.paddr_enabled`

Added `paddr_enabled: bool = False` to `DamonConfig` with docstring explaining
the disabled default and the existing interval settings (`paddr_sample_us`,
`paddr_aggr_us`, `paddr_update_us`). The field is serialized via
`to_primitive()` and parsed from `[damon]` TOML config section via `load()`.

### `groop/src/groop/daemon/paddr_lifecycle.py` — `DaemonPaddrLifecycle`

A small daemon lifecycle owner around the existing `damon/paddr.py` and
`damon/control.py` sources of truth. Key characteristics:

1. **Disabled by default.** When `paddr_enabled` is `False` (the default),
   `start()` is a no-op and performs zero DAMON writes.

2. **Enabled path.** When `paddr_enabled` is `True`, `start()` plans and starts
   exactly one groop-owned whole-host paddr session using the existing
   `plan_start_paddr_session` and `start_planned_paddr_session` functions.
   Operator config acts as authorization (passes `START` as confirmed text).

3. **Idempotent restart.** If a groop-owned paddr marker already exists for the
   same `damon_root`, the lifecycle adopts it rather than allocating a
   duplicate kdamond slot. Foreign sessions (non-groop markers) are never
   touched.

4. **Bounded failure.** `PaddrLifecycleStartError` is raised on failure
   (no free kdamond, root required, ownership conflict). The daemon is expected
   to catch this and continue without paddr.

5. **Graceful shutdown.** `stop()` calls `stop_owned_sessions` with the exact
   kdamond index of this lifecycle's session, stopping only the session owned
   by this daemon run. Foreign sessions are never affected.

6. **Fixture injection seams.** The lifecycle accepts `damon_root`, `state_dir`,
   `require_root`, `is_root`, and `now` parameters for testing.

### `groop/src/groop/daemon/__init__.py` — Public API

Exports `DaemonPaddrLifecycle`, `DamonPaddrLifecycleError`,
`PaddrLifecycleStartError`, `PaddrLifecycleStopError`.

### `groop/src/groop/cli.py` — Daemon Serve Integration

The `_main_daemon` `serve` command creates a `DaemonPaddrLifecycle` instance
after the BPF snapshot bridge setup. If `config.damon.paddr_enabled` is `True`,
it calls `start()` and logs the result. On graceful shutdown (KeyboardInterrupt),
it calls `stop()` in the `finally` block. Uses `getattr(collector, "damon_root",
DEFAULT_DAMON_ROOT)` for compatibility with test mocks.

### `groop/tests/test_daemon_paddr_lifecycle.py` — Focused Tests

13 focused tests covering:

| Test | What it verifies |
|---|---|
| `test_config_paddr_enabled_default_false` | Config default is False |
| `test_config_paddr_enabled_round_trip` | Serialization/deserialization |
| `test_lifecycle_disabled_does_nothing` | Disabled lifecycle is no-op |
| `test_lifecycle_start_stop` | Full start/stop cycle |
| `test_lifecycle_idempotent_adoption` | Existing marker adopted without duplicate |
| `test_lifecycle_stop_only_this_run` | stop() only stops owned session |
| `test_lifecycle_foreign_session_not_touched` | Foreign markers/slots untouched |
| `test_lifecycle_start_failure_no_free_slot` | Bounded failure on busy kdamond |
| `test_lifecycle_start_failure_root_required` | Bounded failure on root check |
| `test_lifecycle_stop_no_session_returns_zero` | Safe no-op stop |
| `test_lifecycle_stop_after_disabled_start` | Safe no-op after disabled start |
| `test_lifecycle_disabled_no_damon_writes` | Zero DAMON writes when disabled |
| `test_lifecycle_properties` | session/started properties reflect state |

## Deviations From Handoff

None. All requirements are met:

- [x] `DamonConfig.paddr_enabled: bool = false`, parse and serialize.
- [x] Small lifecycle owner, no duplication of sysfs write lists.
- [x] When enabled, daemon startup plans/starts exactly one groop-owned paddr
      session. Config = operator authorization.
- [x] Idempotent across already-live groop-owned marker.
- [x] Never adopts/stops/overwrites foreign session.
- [x] Graceful shutdown stops only owned session.
- [x] Bounded startup failure: daemon continues without paddr.
- [x] Fixture injection seams for root, state dir, root check, clock.
- [x] Focused tests: config, lifecycle, ownership/recovery, foreign-session,
      failure, integration.
- [x] No live DAMON mutation in normal suite.
- [x] Updated README, ROADMAP, STATUS, OPERATIONS, DAEMON, RELEASE-READINESS,
      MEASUREMENTS.

## Proposed Contract Changes

None. `CONTRACTS.md` is unchanged. The new module is additive and package-private
to `groop/daemon/`.

## Test Evidence

```bash
# Focused paddr lifecycle tests
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_daemon_paddr_lifecycle.py -q
# 13 passed in 0.22s

# Full suite
PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 446 passed, 1 skipped in 49.25s

# Full-source py_compile
find groop/src/groop groop/tests -name '*.py' -print0 | xargs -0 python3 -m py_compile
# (no output = clean)
```

## Known Gaps / Open Items

- Live-root acceptance of the daemon-owned paddr lifecycle is not run in this
  session. The lifecycle uses the same `plan_start_paddr_session` /
  `start_planned_paddr_session` / `stop_owned_sessions` functions that are
  already fixture-tested by P14/P9/P11.
- The daemon serve integration is tested only by the existing BPF snapshot
  integration test (`test_daemon_enabled_bridge_uses_configured_root_and_shuts_down`)
  which exercises the daemon serve path. A dedicated daemon + paddr lifecycle
  integration test would be a future improvement.
- No `--paddr-enabled` CLI flag was added to `groop daemon serve`; the feature
  is config-only. A CLI flag could be added later for convenience.

## Files Changed

```
M groop/README.md                                (P44 status: Planned -> Done)
M groop/MEASUREMENTS.md                          (P44 evidence)
M groop/docs/DAEMON.md                            (Daemon-owned paddr section)
M groop/docs/OPERATIONS.md                        (paddr_enabled config example)
M groop/docs/RELEASE-READINESS.md                 (non-claim updated)
M groop/docs/ROADMAP.md                           (P44 status: planned -> done)
M groop/docs/STATUS.md                            (v2 %, implemented list, quality gate)
M groop/src/groop/cli.py                          (daemon serve integration)
M groop/src/groop/config.py                       (DamonConfig.paddr_enabled)
M groop/src/groop/daemon/__init__.py              (export new module)
A groop/src/groop/daemon/paddr_lifecycle.py       (DaemonPaddrLifecycle)
A groop/tests/test_daemon_paddr_lifecycle.py      (13 focused tests)
A groop/handoff/reports/P44-LOG.md                (this log)
A groop/handoff/reports/P44-REPORT.md             (this report)
```
