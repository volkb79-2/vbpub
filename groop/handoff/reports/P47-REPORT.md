# P47 Report - Daemon Component Health

**Branch:** `feat/groop-p47-daemon-component-health`

**Latest-main merge:** `a08a464` (main `7737daf`)

**Date:** 2026-07-10

## Result

P47 adds a read-only, typed health snapshot for the daemon-owned collector,
BPF snapshot bridge, and paddr lifecycle. The socket operation is
`{"op":"health"}`; the CLI is `groop daemon health [--json|--pretty-json]`.

Each component exposes one of `disabled`, `starting`, `healthy`, `degraded`,
`failed`, `stopping`, or `stopped`, plus bounded public detail, attempt/success
timestamps, consecutive failed attempts, last error, and a transition count.

## Safety And Compatibility

- Registry updates and snapshots share one lock.
- Detail and errors are sanitized at ingestion: control whitespace is folded,
  common credential assignments and absolute paths are redacted, and UTF-8
  byte limits are enforced (256-byte detail/error, 64-byte code).
- Daemon integration stores static public messages/codes, never raw exception
  strings. Full exceptions remain console/log-only.
- Health responses are capped at 16 KiB by the client.
- `health-v1` parsing fails closed on an unexpected schema/capability,
  component count/order/name, state, timestamp, counter, control character,
  oversized field, or malformed error. Older daemons return the existing
  unsupported-operation response and receive compatible-daemon guidance.
- Component errors are preserved through server, client, and CLI JSON.
- Existing `current` and `stream` response shapes remain unchanged.

## Truthful Lifecycle Wiring

- Collector begins `starting`; only a successful `next(frame_source)` makes it
  healthy. Exhaustion or collection exceptions make it failed without exposing
  exception content.
- BPF initial refresh is degraded only when last-valid data exists; otherwise
  it is failed. Every failed/degraded attempt increments the consecutive count,
  and success resets it.
- BPF shutdown becomes stopped only after the worker actually exits. A live
  worker after the five-second join is failed with `bpf_shutdown_timeout`.
- Paddr started/adopted/failure states follow P44. On shutdown an adopted
  session remains active, as decided; only this daemon lifecycle stops.
- Lifecycle markers record attempt timestamps.

## Tests

The 49 focused tests cover registry transitions, byte bounds and redaction,
concurrency, socket/CLI behavior, strict malformed-response rejection, error
round trips (including invalid UTF-8), direct snapshot safety, collector
truthfulness, and actual daemon-serve collector/BPF
integration. The serve tests cover initial BPF failures with and without a
last-valid snapshot and a worker that remains alive after its join deadline.

```bash
PYTHONPATH=groop/src /tmp/p43-clean-venv/bin/python -m pytest \
  groop/tests/test_daemon_component_health.py -q
# 49 passed in 3.47s

PYTHONPATH=groop/src /tmp/p43-clean-venv/bin/python -m pytest \
  groop/tests/test_daemon_component_health.py \
  groop/tests/test_daemon_broker.py \
  groop/tests/test_daemon_client.py \
  groop/tests/test_daemon_bpf_snapshot.py \
  groop/tests/test_daemon_paddr_lifecycle.py \
  groop/tests/test_inspect_files.py -q
# 238 passed in 5.98s

PYTHONPATH=groop/src /tmp/p43-clean-venv/bin/python -m pytest groop/tests -q
# 672 passed, 1 skipped in 50.05s
```

Changed-source `py_compile` and `git diff --check` are clean.

## Scope Boundary

This is local, read-only health reporting. It does not add mutating RPCs,
remote/TCP access, live BPF load/attach, or restart loops. P51 remains the
request-independent producer/fan-out package; P52 remains the broader versioned
read API for separate frontends.
