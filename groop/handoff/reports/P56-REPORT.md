# P56 — `groop squeeze` (Guided Working-Set Measurement) — Implementation Report

## Summary

Implemented `groop squeeze --target CGROUP_PATH --admin --confirm SQUEEZE`: a
guided, stepped `memory.high` squeeze that measures a cgroup's real (hot+warm)
working set under pressure, absorbing into groop natively the workflow proven
live by `scripts/gstammtisch-guide/files/usr/local/sbin/container-mempress.sh`.

Direct cgroupfs writes (not `systemctl set-property`), gated through the P46
root/admin/typed-confirmation/audit posture, with mandatory `memory.high`
restore on exit/SIGINT/SIGTERM, a P2-compatible JSONL log, per-session audit
(two records, not one per step), and 31 focused tests.

## What was built

### New module: `groop/src/groop/actions/squeeze.py`

- `parse_size(text)` — parse human-readable size strings (256M, 1G, 4096)
- `SqueezeConfig(frozen dataclass)` — configuration for one squeeze run (target,
  step, delay, floor, start, relax_to, PSI/refault limits, force, log/audit
  paths, admin/confirm)
- `SqueezeStep(frozen dataclass)` — one squeeze step sample (memory.current/anon/
  zswapped/z_pool/swap, PSI some/full avg10, refaults/s, timestamp)
- `SqueezeResult(frozen dataclass)` — typed result (stop reason, squeeze point,
  steps, restored_to, error)
- `_RestoreGuard` — context manager with signal handler registration for SIGINT
  and SIGTERM that restores `memory.high` on any exit path; injectable
  signal_handler seam for tests (no real OS signal delivery in the default suite)
- `run_squeeze()` — core squeeze loop with all injectable cgroup reader/writer/
  clock/auditor seams:
  - Reads `memory.current` and `memory.min` via `read_int()`
  - Refuses targets with `memory.min > 0` unless `--force`
  - Computes start high: user-provided or current rounded up to step boundary
  - Loop: write `memory.high` → sleep → sample (current, stat, zswap, swap,
    pressure) → derive refaults/s from cumulative counter delta → check stop
    conditions (PSI some/full avg10 > limit, refaults/s > limit, floor reached)
  - Stop records the **last** non-pressure `memory.high` as `squeeze_point`
- `run_squeeze_gated()` — P46-style gate chain (admin mode, `--confirm SQUEEZE`,
  root check) before delegating to `run_squeeze()`
- JSONL log helpers: header (target, parameters, start time), step (per-sample),
  summary (stop reason, squeeze point, restored-to value) — schema-compatible
  with P2 header+frame JSONL convention
- `render_squeeze_result()` and `squeeze_result_to_jsonable()` for output

### Modified: `groop/src/groop/actions/__init__.py`

- Exports `SqueezeConfig`, `SqueezeResult`, `SqueezeStep`, `parse_size`,
  `render_squeeze_result`, `run_squeeze`, `run_squeeze_gated`,
  `squeeze_result_to_jsonable`

### Modified: `groop/src/groop/cli.py`

- `_default_squeeze_log_path(target)` — default log under `/var/log/groop/squeeze/`
- `parse_squeeze_args(argv)` — argparse with all options:
  `--target`, `--admin`, `--confirm`, `--step` (256M), `--delay` (15),
  `--floor` (1G), `--start`, `--relax-to` (max), `--psi-some-limit` (10),
  `--psi-full-limit` (5), `--rf-limit` (200), `--log`, `--json`, `--force`,
  `--audit-path`
- `_main_squeeze(argv)` — CLI entry point that parses args, validates
  parameters, builds `SqueezeConfig`, calls `run_squeeze_gated()`, renders result
- `main()`: dispatch `"squeeze"` to `_main_squeeze`

### Tests: `groop/tests/test_squeeze.py` — 31 tests

| Test count | Area |
|---|---|
| 6 | `parse_size` validation (bytes, K, M, G, invalid, non-string) |
| 4 | `run_squeeze_gated` gates (admin false, confirm wrong, root false, all pass) |
| 2 | `memory.min > 0` refusal and `--force` override |
| 1 | Happy path full squeeze to floor with JSONL log shape |
| 4 | Stop conditions (PSI some, PSI full, refault rate, floor) |
| 3 | SIGINT safety (restore via `_RestoreGuard`, idempotency, signal installation) |
| 2 | JSONL log shape (header/step/summary, error with no steps) |
| 3 | Result rendering (error text, success text, jsonable) |
| 4 | CLI arg parsing (defaults, custom values, target required, admin gate) |
| 2 | Audit logging (session audit written, no subprocess import) |

All tests use injected readers/writers with no real cgroupfs mutation.

### Docs updated

- `STATUS.md` — v2 65-70% → 70-75%, P56 in Implemented
- `ROADMAP.md` — P56 marked done with detailed description and two-run
  stratification guidance
- `OPERATIONS.md` — squeeze safety model entry with CLI examples and
  two-run stratification pattern
- `RELEASE-READINESS.md` — squeeze non-claims added

## Deviations from handoff

None. All named requirements are met:

1. ✅ `groop squeeze --target CGROUP_PATH --admin --confirm TEXT [options]`
   dispatched like other subcommands (own `parse_squeeze_args`/`_main_squeeze`)
2. ✅ Options match `container-mempress.sh` proven defaults
3. ✅ Protocol mirrors the script step-for-step; `memory.min > 0` refusal unless
   `--force`; stop on PSI/refault/floor; squeeze point = last non-pressure value
4. ✅ Hard safety: `memory.high` always restored via `_RestoreGuard`
   (try/finally + signal handlers for SIGINT/SIGTERM)
5. ✅ Headered JSONL log with header/step/summary records, P2-compatible
6. ✅ Per-session audit (start + end) via `AuditLog`
7. ✅ 31 fixture-cgroup-tree tests: happy path, gate refusals, all four stop
   conditions, SIGINT restore, log shape, no real subprocess/cgroupfs
8. ✅ Two-run stratification documented in OPERATIONS.md
9. ✅ Docs updated (STATUS, ROADMAP, OPERATIONS, RELEASE-READINESS)

## Proposed contract changes

None. The squeeze module is additive and package-private (`groop/actions/`).
It reuses `read_int`/`read_flat_kv`/`read_pressure` from `groop.collect.cgroup`
and `AuditLog` from `groop.actions.audit`.

## Test evidence

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_squeeze.py -q
31 passed in 0.32s

PYTHONPATH=groop/src python3 -m pytest groop/tests -q
945 passed, 2 skipped, 1 warning in 122.23s

PYTHONPATH=groop/src python3 -m py_compile \
  groop/src/groop/actions/squeeze.py \
  groop/src/groop/actions/__init__.py \
  groop/src/groop/cli.py \
  groop/tests/test_squeeze.py
# All compiled successfully

git diff --check
# clean
```

The one warning is a pre-existing environment issue
(`DeprecationWarning: jsonschema.exceptions.RefResolutionError` from
schemathesis plugin), not related to P56.

## Known gaps / open items

- The `_RestoreGuard` context manager installs real signal handlers in
  production; the injectable `signal_handler` seam makes it testable without
  real OS signal delivery. The guard is tested directly (signal handler
  installation + restore behavior).
- Live destructive acceptance (actual cgroupfs writes as root) was not run.
  All tests use injected readers/writers and assert observable artifacts
  without host mutation.
- The default `--audit-path` points at `/var/log/groop/actions.jsonl` but
  squeeze uses `AuditLog.record()` (not the P46 execution audit path) for its
  session start/end records. If a future policy wants these in a separate audit
  file, the CLI `--audit-path` is the override.
