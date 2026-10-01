---
schema_version: 1
id: topos-P129-paddr-lifecycle-coverage
project: topos
title: "Complete daemon paddr lifecycle failure coverage"
tier: haiku-high
input_revision: "af3d2bba"
depends_on: [topos-P128-banner-residual]
session: "resume:topos-daemon-coverage"
source: {kind: product-goal, ref: "global-coverage-healing"}
scope:
  touch:
    - "topos/tests/test_daemon_paddr_lifecycle.py"
    - "topos/nyxloom-trove/handoffs/topos-P129-paddr-lifecycle-coverage.md"
  forbid:
    - "topos/src/topos/daemon/paddr_lifecycle.py"
    - "topos/src/topos/damon/control.py"
    - "topos/nyxloom-trove/nyxloom.toml"
    - "topos/tools/coverage_gate.py"
oracles:
  - id: O1
    observable: "Injected bounded DAMON start and stop failures are translated to PaddrLifecycleStartError/PaddrLifecycleStopError without silently reporting a successful lifecycle operation."
    negative: "A raw DamonControlError escapes or a failed stop is treated as a completed shutdown."
    gate: topos-suite
  - id: O2
    observable: "Malformed, mismatched, non-paddr, non-live, missing-operations, non-object, and non-numeric marker cases are refused or ignored according to the public start() contract; no foreign or malformed marker is deleted."
    negative: "A malformed marker is adopted/deleted, a foreign-mode marker blocks a fresh start, or invalid DAMON state/operations is accepted."
    gate: topos-suite
  - id: O3
    observable: "The full tester-unified xdist coverage JSON reports no missing line or branch for daemon/paddr_lifecycle.py."
    negative: "Only focused or rounded coverage is reported, leaving an error path unexecuted."
    gate: topos-suite
gates: [topos-suite]
escalate_if:
  - "a named contract cannot be met as specified"
  - "scope requires a forbidden file"
---

# P129 — Complete daemon paddr lifecycle failure coverage

Add tests only. These are filesystem-backed fixture tests; never touch real
DAMON sysfs and do not weaken the lifecycle's fail-closed behavior.

## Context to read first

1. `topos/src/topos/daemon/paddr_lifecycle.py`, especially `start`, `stop`,
   `_find_existing_topos_paddr`, `_read_marker_payload`, and `_marker_index`.
2. `topos/tests/test_daemon_paddr_lifecycle.py` in full: reuse `_damon_root`,
   `_lifecycle`, `_write_marker`, and its existing adoption/failure patterns.
3. `topos/nyxloom-trove/nyxloom.toml`: `[gates.topos-suite]`.

## Work

Append focused public-lifecycle tests in `test_daemon_paddr_lifecycle.py` to
cover these exact contracts. Use `pytest.MonkeyPatch` / `monkeypatch` only at
the lifecycle module boundary; assertions must inspect exceptions, lifecycle
state, marker persistence, and fixture files, never mock the method under test.

1. Patch `start_planned_paddr_session` in `topos.daemon.paddr_lifecycle` to
   raise `DamonControlError("start broke")`; `lc.start()` must raise
   `PaddrLifecycleStartError` containing `cannot start paddr session` and not
   set `started`/`session`.
2. Start a real fixture lifecycle, then patch that module's `stop_owned_sessions`
   to raise `DamonControlError("stop broke")`; `lc.stop()` must raise
   `PaddrLifecycleStopError` containing `cannot stop paddr session` and retain
   its started session for a caller to retry.
3. A top-level marker with `owner="topos"`, `mode="paddr"`, and empty
   `damon_root` must fail closed with `no valid damon_root` and remain present.
   A marker whose `kdamond_idx` is boolean `True` must fail closed as invalid
   (booleans are not valid indexes) and remain present.
4. A top-level marker with a different `mode` (e.g. `vaddr`) is ignored: a
   fresh lifecycle may start its free slot and the mismatched marker remains.
5. For a matching top-level paddr marker, prove each refusal: state `paused`
   raises `unexpected state`; deleting its `contexts/0/operations` file raises
   `no operations path`; and a marker whose JSON body is `[]` raises `must
   contain a JSON object`. Each refusal retains the marker.
6. Create a `kdamond-not-a-number.json` marker with otherwise valid top-level
   payload for idx 0; start must raise index mismatch and retain it. This must
   exercise the real filename parser's `ValueError` fallback, not a private
   helper directly.

Do not introduce production changes, coverage pragmas, arbitrary sleeps, or
private-helper unit tests.

## Oracles

Run the full declared gate in `tester-unified`:

```bash
docker run --rm --mount type=bind,src=/home/vb/volkb79-2/vbpub,dst=/workspaces/vbpub tester-unified:local bash -c "set -euo pipefail; cd /workspaces/vbpub/.worktrees/feat/topos-P129-paddr-lifecycle-coverage; export PYTHONPATH=topos/src:topos; /opt/tester-venv/bin/python -m pytest topos/tests -q -n auto --cov=topos/src/topos --cov-branch --cov-report=json:/tmp/topos-coverage.json && /opt/tester-venv/bin/python topos/tools/coverage_gate.py --repo . --base main --coverage-json /tmp/topos-coverage.json --source topos/src/topos"
```

The gate must pass and coverage JSON must have empty `missing_lines` and
`missing_branches` for `topos/src/topos/daemon/paddr_lifecycle.py`.

## Scope / forbid

Touch only the declared handoff and test file. Do not modify source, test
helpers outside this file, configuration, coverage tooling, dependencies, or
the existing happy-path tests.

## BLOCKED rule

If any named public lifecycle contract cannot be reached with the fixture
helpers, or satisfying it needs a forbidden file, STOP. Write `BLOCKED:
<specific reason>` to the handoff LOG, commit that log-only change, and exit.
Do not create a real-DAMON dependency or weaken the safety checks.
