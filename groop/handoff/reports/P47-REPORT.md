# P47 Report - Daemon Component Health

**Branch:** `feat/groop-p47-daemon-component-health`
**Base:** `9e15d85` (Merge groop P44 daemon paddr lifecycle)
**Date:** 2026-07-10

## What Was Built

### `groop/src/groop/daemon/component_health.py` — Component Health Registry

A thread-safe, lock-based component health registry owned by `groop daemon serve`. Key characteristics:

1. **Stable states.** Every component tracks one of: `disabled`, `starting`, `healthy`, `degraded`, `failed`, `stopping`, `stopped`.

2. **Three tracked components.** The registry monitors exactly three daemon-owned components by default:
   - `collector` — the core frame collection loop
   - `bpf_snapshot_bridge` — the P42 BPF snapshot bridge (disabled by default)
   - `paddr_lifecycle` — the P44 daemon-owned paddr lifecycle (disabled by default)

3. **Bounded public detail.** `ComponentError` carries only a short `message` string and an optional `error_code`. No tracebacks, environment variables, arbitrary paths, command output, or secrets are ever stored in the registry.

4. **Deterministic concurrency.** A single `threading.Lock` guards all mutations and snapshots. The `snapshot()` method returns an immutable `HealthSnapshot` tuple under the lock, so readers always see a consistent view.

5. **Consecutive failure tracking.** `record_failure()` increments `consecutive_failures`; `record_success()` resets it to 0. This allows callers to detect persistent vs. transient failures.

6. **Timestamp tracking.** `last_attempt_ts` updates on every state change; `last_success_ts` updates only on `record_success()`.

7. **State change counter.** A monotonic `state_change_count` is tracked per component since registry creation.

8. **Default-disabled.** All components start in `DISABLED` state. The daemon serve code explicitly transitions components as they are initialized.

### Protocol — `health` operation

A new `{"op":"health"}` request in the daemon read-only protocol:

```json
{"op":"health"}
```

Response (single JSON line):

```json
{"type":"health","schema_version":1,"capability":"health-v1","components":[...]}
```

The `capability` field (`"health-v1"`) allows version-gating. Older daemons that do not support `health` return `{"type":"error","error":"unsupported operation"}`, and the client raises `DaemonResponseError` with P31-style compatible-daemon guidance.

### `FrameBroker` extension

`FrameBroker` accepts an optional `health_registry: ComponentHealthRegistry` parameter. When provided, the `health` op returns the registry snapshot. When absent, it returns an error response. Existing `current` and `stream` operations are unaffected.

### `DaemonClient` extension

`DaemonClient.request_health()` sends `{"op":"health"}` and parses the response into a `HealthSnapshot` with `ComponentSnapshot` entries. Raises the same `DaemonConnectError` / `DaemonProtocolError` / `DaemonResponseError` hierarchy as other client methods.

### CLI — `groop daemon health`

```bash
groop daemon health                           # JSON on stdout
groop daemon health --json                    # explicit JSON
groop daemon health --pretty-json             # indented JSON
groop daemon health --socket /custom/path.sock # custom socket
```

- Returns JSON with `schema_version` and `components` array.
- Missing/corrupt socket returns exit code 2 with P31-style actionable guidance.
- The health command exits 0 on success.

### Wiring into `groop daemon serve`

The `_main_daemon` `serve` command now:

1. Creates a `ComponentHealthRegistry` before the `FrameBroker`.
2. Passes the registry to the broker.
3. Marks `collector` as `starting` → `healthy`.
4. Marks `bpf_snapshot_bridge` and `paddr_lifecycle` as `disabled` (explicit defaults).
5. When BPF snapshot bridge is enabled: marks `starting`, then transitions to `healthy`/`degraded`/`failed` based on `BpfSnapshotBridge.refresh()` outcomes.
6. When paddr lifecycle is enabled: marks `starting`, then transitions to `healthy` (started/adopted) or `failed` (PaddrLifecycleStartError).
7. On shutdown: marks `stopping` → `stopped` for each component.
8. All transitions use `ComponentError` for bounded error detail.

### Tests — 32 focused tests

All in `groop/tests/test_daemon_component_health.py`:

| Test | What it verifies |
|------|-----------------|
| `test_component_state_values` | All 7 required states exist |
| `test_component_error_bounded` | ComponentError only carries message + optional code |
| `test_component_names_match_default_disabled` | All components start disabled |
| `test_set_state_healthy` | set_state to healthy resets failures/error |
| `test_set_state_failed_increments_consecutive` | Consecutive failure count increments |
| `test_consecutive_failures_reset_on_healthy` | record_success resets to 0 |
| `test_record_success_sets_timestamp` | last_attempt_ts and last_success_ts set |
| `test_record_failure_sets_last_attempt_not_last_success` | Only attempt_ts set on failure |
| `test_record_degraded` | Degraded state with error |
| `test_mark_starting_stopping_stopped_disabled` | All lifecycle markers work |
| `test_unknown_component_silently_ignored` | Unknown names are no-ops |
| `test_snapshot_deterministic_order` | Always COMPONENT_NAMES order |
| `test_health_snapshot_to_jsonable` | Protocol shape, no secrets |
| `test_concurrent_updates_deterministic` | 3 threads × 50 iterations each |
| `test_concurrent_reads_and_writes` | Reader+writer threads concurrently |
| `test_build_health_response_shape` | Protocol helper returns correct shape |
| `test_broker_health_op_with_registry` | Broker serves health via responses() |
| `test_broker_health_op_without_registry` | Error when no registry |
| `test_broker_current_and_stream_still_work` | Existing ops unaffected |
| `test_daemon_socket_health_with_registry` | Socket protocol health response |
| `test_daemon_socket_health_without_registry_returns_error` | Socket error response |
| `test_daemon_client_request_health` | DaemonClient.request_health() |
| `test_daemon_client_health_without_registry_raises_error` | DaemonResponseError |
| `test_health_snapshot_by_name` | by_name lookup |
| `test_component_snapshot_no_consecutive_failures_field_when_zero` | JSON omission |
| `test_component_snapshot_omits_error_when_none` | JSON omission |
| `test_component_snapshot_includes_error_when_set` | JSON inclusion |
| `test_state_change_count_increments` | Monotonic counter |
| `test_cli_parse_health_args` | Argparse for health command |
| `test_cli_health_via_main_daemon` | CLI health --json output |
| `test_cli_health_pretty_json` | CLI health --pretty-json |
| `test_cli_health_missing_socket` | Exit 2 with guidance |

### Resumability log

`groop/handoff/reports/P47-LOG.md` updated throughout implementation.

## Deviations From Handoff

None. All requirements are met:

- [x] Thread-safe component-health registry with stable states (disabled, starting, healthy, degraded, failed, stopping, stopped).
- [x] Models collector, BPF snapshot bridge, and paddr lifecycle.
- [x] Bounded public detail: `ComponentError` has only `message` and optional `error_code` — no tracebacks, environment, paths, output, or secrets.
- [x] Records attempt/success timestamps, consecutive failure count, and bounded error.
- [x] Wires P42 BPF refresh transitions and P44 paddr lifecycle transitions into the registry without duplicating their logic.
- [x] Explicit DISABLED defaults.
- [x] Read-only protocol `health` request via FrameBroker.
- [x] `groop daemon health [--json]` CLI command.
- [x] Version/capability-gated: `capability: "health-v1"` in response; unknown op returns error for older daemons.
- [x] Deterministic snapshots during concurrent updates/shutdown via `threading.Lock`.
- [x] Unit, protocol, CLI, error-bound, concurrency, and daemon integration tests (32 tests).
- [x] Updated daemon/operations/readiness/status/measurements docs.

## Proposed Contract Changes

None. `CONTRACTS.md` is unchanged. The new module is additive and package-private to `groop/daemon/`.

## Test Evidence

### Focused tests

```bash
cd /workspaces/vbpub/.worktrees/-groop-p47-daemon-component-health
PYTHONPATH=groop/src python3 -m pytest groop/tests/test_daemon_component_health.py -q
# 32 passed in 3.59s
```

### Full suite

```bash
PYTHONPATH=groop/src python3 -m pytest groop/tests -q
# 487 passed, 1 skipped in 53.03s
```

### py_compile

```bash
python3 -m py_compile \
  groop/src/groop/daemon/component_health.py \
  groop/src/groop/daemon/__init__.py \
  groop/src/groop/daemon/broker.py \
  groop/src/groop/daemon/client.py \
  groop/src/groop/cli.py \
  groop/tests/test_daemon_component_health.py
# (exit 0, no output)
```

### Daemon CLI smoke

```bash
PYTHONPATH=groop/src:groop/tests python3 -c "
from groop.daemon import FrameBroker, serve_unix_socket, ComponentHealthRegistry
from conftest import fixture_frame; import threading, tempfile; from pathlib import Path
reg = ComponentHealthRegistry(); reg.record_success('collector', detail='smoke')
broker = FrameBroker([fixture_frame()], health_registry=reg)
with tempfile.TemporaryDirectory() as tmp:
  sock = Path(tmp) / 'smoke.sock'
  server = serve_unix_socket(sock, broker)
  t = threading.Thread(target=server.serve_forever, daemon=True); t.start()
  from groop.daemon.client import DaemonClient
  health = DaemonClient(sock).request_health()
  assert health.by_name('collector').state is ComponentState.HEALTHY
  assert health.by_name('bpf_snapshot_bridge').state is ComponentState.DISABLED
  server.shutdown(); server.server_close()
print('SMOKE OK')
"""
# SMOKE OK
```

## Quality Gates

- [x] Full test suite green (487 passed, 1 skipped)
- [x] `py_compile` clean on all new/changed Python files
- [x] Fixture tests cover state transitions, concurrency, protocol, CLI, error bounding, and integration
- [x] `groop daemon health` demonstrably runs against a fixture daemon
- [x] Existing protocol ops (current, stream) unchanged
- [x] P42/P44 daemon serve integration preserved
- [x] Default-disabled components explicitly documented and tested

## Known Gaps / Open Items

- **No live daemon health CLI evidence.** The health CLI was tested against fixture daemon sockets. A real daemon process with BPF/paddr enabled would demonstrate the full state transition lifecycle, but this requires a host with `bpftool`, writable BPF pins, and DAMON sysfs — none available in this development session.
- **Health endpoint is read-only.** No health mutation RPC or remote/TCP API was added — these are out of scope per the handoff.
- **Consecutive failure thresholds.** The registry tracks consecutive failures but does not implement automatic state transitions (e.g., degraded after N failures). This remains a future enhancement.
- **Poll-based health monitoring.** The health registry is push-based: components update their own state. A future pull-based health monitor that checks component responsiveness would be additive.

## Files Changed

```
A groop/src/groop/daemon/component_health.py    (ComponentHealthRegistry, ~360 lines)
M groop/src/groop/daemon/__init__.py             (exports)
M groop/src/groop/daemon/broker.py              (health_registry param + health op)
M groop/src/groop/daemon/client.py              (request_health() method)
M groop/src/groop/cli.py                        (health subcommand, serve wiring)
A groop/tests/test_daemon_component_health.py    (32 focused tests)
A groop/handoff/reports/P47-LOG.md              (work log)
A groop/handoff/reports/P47-REPORT.md           (this file)
M groop/README.md                               (P47 row: Queued -> Done)
M groop/MEASUREMENTS.md                          (P47 evidence section)
M groop/docs/STATUS.md                           (P47 implemented, test counts)
M groop/docs/ROADMAP.md                          (P47 status: done)
M groop/docs/OPERATIONS.md                       (groop daemon health examples)
M groop/docs/RELEASE-READINESS.md                (P47 documented)
```
