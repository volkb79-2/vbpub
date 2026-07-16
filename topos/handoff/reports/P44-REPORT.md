# P44 Report - Daemon-Owned paddr Lifecycle

**Branch:** `feat/topos-p44-daemon-paddr-lifecycle`
**Base:** `9d6327b` (docs(topos): carve P44-P46 v2 safety slices)
**Date:** 2026-07-10 (review fix round)

## What Was Built

### `topos/src/topos/config.py` — `DamonConfig.paddr_enabled`

Added `paddr_enabled: bool = False` to `DamonConfig` with docstring explaining
the disabled default and the existing interval settings (`paddr_sample_us`,
`paddr_aggr_us`, `paddr_update_us`). The field is serialized via
`to_primitive()` and parsed from `[damon]` TOML config section via `load()`.

**Review fix:** `paddr_enabled` now only accepts a real TOML boolean (Python
`bool`).  A TOML string like `"true"` is rejected and silently defaults to
`False`, avoiding the `bool()` truthiness pitfall.

### `topos/src/topos/daemon/paddr_lifecycle.py` — `DaemonPaddrLifecycle`

A small daemon lifecycle owner around the existing `damon/paddr.py` and
`damon/control.py` sources of truth. Key characteristics:

1. **Disabled by default.** When `paddr_enabled` is `False` (the default),
   `start()` is a no-op and performs zero DAMON writes.

2. **Enabled path.** When `paddr_enabled` is `True`, `start()` plans and starts
   exactly one topos-owned whole-host paddr session using the existing
   `plan_start_paddr_session` and `start_planned_paddr_session` functions.
   Operator config acts as authorization (passes `START` as confirmed text).

3. **Idempotent restart with verification.** If a topos-owned paddr marker
   already exists for the same `damon_root`, the lifecycle verifies the
   referenced kdamond slot is live (state `on`, operations `paddr`) before
   adopting. A stale marker (kdamond `off`) is cleaned up; a malformed,
   unreadable, or internally inconsistent marker fails closed and is retained;
   a marker pointing at a missing
   kdamond or a kdamond running a different monitoring mode raises
   `PaddrLifecycleStartError`.

4. **Bounded failure.** `PaddrLifecycleStartError` is raised on failure
   (no free kdamond, root required, ownership conflict, stale/malformed marker,
   or kdamond mismatch). The daemon is expected to catch this and continue
   without paddr.

5. **Graceful shutdown.** `stop()` tears down a session created by the current
   run using its exact kdamond index. A verified session adopted from an
   earlier run remains persistent and requires explicit
   `topos damon stop --all-mine` cleanup. Foreign sessions are never affected.

6. **Fixture injection seams.** The lifecycle accepts `damon_root`, `state_dir`,
   `require_root`, `is_root`, and `now` parameters for testing.

7. **PaddrLifecycleOutcome enum.** `start()` sets `outcome` to
   `DISABLED`/`STARTED`/`ADOPTED` so callers can print truthful messages.

### `topos/src/topos/damon/control.py` — `owned_markers()` public API

Added a public `owned_markers(state_dir)` function so the lifecycle module does
not need to import the private `_owned_markers` / `_read_json` helpers.

### `topos/src/topos/daemon/__init__.py` — Public API

Exports `DaemonPaddrLifecycle`, `DamonPaddrLifecycleError`,
`PaddrLifecycleStartError`, `PaddrLifecycleStopError`.

### `topos/src/topos/cli.py` — Daemon Serve Integration

The `_main_daemon` `serve` command creates a `DaemonPaddrLifecycle` instance
after the BPF snapshot bridge setup. If `config.damon.paddr_enabled` is `True`,
it calls `start()` and uses `match paddr_lifecycle.outcome` to print truthful
"started" / "adopted" messages. On graceful shutdown (KeyboardInterrupt), it
calls `stop()` in the `finally` block.

### `topos/tests/test_daemon_paddr_lifecycle.py` — Focused Tests

22 focused tests covering:

| Test | What it verifies |
|---|---|
| `test_config_paddr_enabled_default_false` | Config default is False |
| `test_config_paddr_enabled_round_trip` | Serialization/deserialization |
| `test_config_paddr_enabled_string_is_not_truthy` | TOML string "true" rejected |
| `test_lifecycle_disabled_does_nothing` | Disabled lifecycle is no-op |
| `test_lifecycle_start_stop` | Full start/stop cycle |
| `test_lifecycle_idempotent_adoption` | Existing marker adopted without duplicate |
| `test_lifecycle_stop_only_this_run` | stop() only stops owned session (multi-slot) |
| `test_lifecycle_foreign_session_not_touched` | Foreign markers/slots untouched |
| `test_lifecycle_stale_marker_cleaned_up` | Stale (off-state) marker cleaned up |
| `test_lifecycle_malformed_marker_fails_closed` | Invalid JSON marker retained; no writes |
| `test_lifecycle_marker_index_mismatch_fails_closed` | Filename/payload mismatch retained; no slots touched |
| `test_lifecycle_wrong_operations_raises_error` | vaddr kdamond refused for paddr marker |
| `test_lifecycle_missing_kdamond_slot_raises_error` | Non-existent slot raises error |
| `test_lifecycle_adopted_live_session` | Live kdamond adopted successfully |
| `test_lifecycle_stale_marker_diff_damon_root_ignored` | Different damon_root marker ignored |
| `test_lifecycle_start_failure_no_free_slot` | Bounded failure on busy kdamond |
| `test_lifecycle_start_failure_root_required` | Bounded failure on root check |
| `test_lifecycle_stop_no_session_returns_zero` | Safe no-op stop |
| `test_lifecycle_stop_after_disabled_start` | Safe no-op after disabled start |
| `test_lifecycle_disabled_no_damon_writes` | Zero DAMON writes when disabled |
| `test_lifecycle_properties` | session/started/outcome properties |
| `test_lifecycle_daemon_serve_integration` | Daemon serve CLI lifecycle wiring |

## Deviations From Handoff

None. All requirements are met, plus the review-fix additions:

- [x] `DamonConfig.paddr_enabled: bool = false`, parse and serialize.
- [x] Small lifecycle owner, no duplication of sysfs write lists.
- [x] When enabled, daemon startup plans/starts exactly one topos-owned paddr
      session. Config = operator authorization.
- [x] Idempotent across already-live topos-owned marker (with kdamond
      validation).
- [x] Never adopts/stops/overwrites foreign session.
- [x] Graceful shutdown stops current-run sessions and leaves adopted sessions persistent.
- [x] Bounded startup failure: daemon continues without paddr.
- [x] Fixture injection seams for root, state dir, root check, clock.
- [x] Focused tests: config, lifecycle, ownership/recovery, foreign-session,
      failure, validation, integration.
- [x] No live DAMON mutation in normal suite.
- [x] Updated README, ROADMAP, STATUS, OPERATIONS, DAEMON, RELEASE-READINESS,
      MEASUREMENTS.

## Proposed Contract Changes

None. `CONTRACTS.md` is unchanged. The new module is additive and package-private
to `topos/daemon/`.

## Test Evidence

```bash
# Focused paddr lifecycle tests
PYTHONPATH=topos/src python3 -m pytest topos/tests/test_daemon_paddr_lifecycle.py -q
# 22 passed in 0.17s

# Full suite
PYTHONPATH=topos/src python3 -m pytest topos/tests -q
# 455 passed, 1 skipped in 46.99s

# Full-source py_compile
find topos/src/topos topos/tests -name '*.py' -print0 | xargs -0 python3 -m py_compile
# (no output = clean)
```

## Known Gaps / Open Items

Post-merge controller validation with P46 on main passed the combined focused
regression (`151 passed`) and full suite (`554 passed, 1 skipped`).

- Live-root acceptance of the daemon-owned paddr lifecycle is not run in this
  session. The lifecycle uses the same `plan_start_paddr_session` /
  `start_planned_paddr_session` / `stop_owned_sessions` functions that are
  already fixture-tested by P14/P9/P11.
- No `--paddr-enabled` CLI flag was added to `topos daemon serve`; the feature
  is config-only. A CLI flag could be added later for convenience.

## Files Changed (review fix round)

```
M topos/src/topos/config.py                          (real TOML bool parsing)
M topos/src/topos/damon/control.py                   (public owned_markers())
M topos/src/topos/daemon/paddr_lifecycle.py          (kdamond validation, outcome enum, fail-closed marker parsing)
M topos/src/topos/cli.py                             (outcome-based messages)
M topos/tests/test_daemon_paddr_lifecycle.py          (22 focused lifecycle tests)
M topos/docs/DAEMON.md                                (updated validation contract)
M topos/handoff/reports/P44-LOG.md                   (this log)
M topos/handoff/reports/P44-REPORT.md                (this report)
```
