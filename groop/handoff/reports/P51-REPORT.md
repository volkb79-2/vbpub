# P51 Report — Daemon-Owned Sampling And Fan-Out

**Branch:** `feat/groop-p51-daemon-sampling-fanout`
**Base:** `b5ba9af` (docs(groop): carve P51 daemon sampling fanout)
**Date:** 2026-07-10

**Merged to main:** `152b686`

## What Was Built

### `groop/src/groop/daemon/broker.py` — Refactored `FrameBroker`

Complete rewrite of the frame broker with a request-independent background
producer. Key changes:

1. **Background producer thread.** A single daemon thread continuously advances
   the frame source, publishing each frame into a bounded sequenced history.
   Read operations (`current`, `stream`) never call `next()` on the source.

2. **`start()`/`stop()`/`join()` lifecycle.** Startup is atomic under concurrent
   callers. Production sleep is interruptible; a blocking arbitrary iterator
   yields a typed bounded join-timeout instead of a false clean shutdown.

3. **`current()` returns the latest published frame.** Before the first frame
   it waits for a bounded startup timeout (default 5 s) and raises
   `FrameUnavailableError` on timeout, source exhaustion, or producer failure.
   After the first frame it returns the most recent frame without blocking.

4. **`stream()` with sequence/cursor semantics.** Without a cursor, returns the
   tail of history (most recent *limit* frames). With a cursor, returns frames
   strictly after the cursor sequence number. Each response includes a `seq`
   field.

5. **Typed terminal state.** Exhaustion, failure, stop, startup timeout, and
   shutdown timeout persist without exposing raw producer exceptions. Last
   valid frames remain readable while P47 health reports terminal failure.

6. **Bounded history.** Configurable `history_size` (default 120), oldest frames
   are evicted via `deque(maxlen=...)`.

7. **Bounded clients and history gaps.** Strict request size/time/client/limit
   caps and cursor metadata prevent slow clients or evicted history from being
   silently replayed.

### `groop/src/groop/cli.py` — Lifecycle Integration

The daemon configures all providers, starts one broker producer before serving,
and stops/joins it on shutdown. P47 collector health is updated only by real
collection success/failure.

### `groop/src/groop/daemon/__init__.py` — Exports

`FrameBrokerError`, `FrameUnavailableError`, `FrameProducerError` exported.

### Tests — `groop/tests/test_daemon_p51.py`

Fourteen focused P51 tests plus the existing daemon/client/health/record tests cover:

| Category | Tests | What They Cover |
|---|---|---|
| Lifecycle | Atomic start, interruptible production stop, typed blocked-source timeout |
| Producer state | Persistent failure/exhaustion, last-valid current, P47 health |
| Fan-out | Repeated-current freshness and two-client non-consuming sequences |
| History | Bounded eviction, cursor continuation, explicit gap metadata |
| Protocol | Strict request/response validation and non-replaying polling client |
| Resources | Bounded clients, request bytes/time, socket cleanup, warnings-as-errors |

### Documentation

- **DAEMON.md**: Background producer section, protocol updated with cursor
  support, error types documented.
- **STATUS.md**: P51 marked as implemented instead of queued.
- **ARCHITECTURE.md**: "Daemon Producer & Fan-Out" section replaces P16 spike
  text; module map updated.

## Test Results

```text
$ PYTHONPATH=groop/src /tmp/p43-clean-venv/bin/python -m pytest groop/tests -q -W error
692 passed, 1 skipped in 53.29s
```

Full-source `py_compile` clean on all changed files:

- `groop/src/groop/daemon/broker.py`
- `groop/src/groop/daemon/__init__.py`
- `groop/src/groop/cli.py`
- `groop/tests/test_daemon_p51.py`
- `groop/tests/test_daemon_broker.py`
- `groop/tests/test_daemon_client.py`

## Requirement Coverage

| Handoff Requirement | Status |
|---|---|
| Refactor FrameBroker into lifecycle with background producer | Done |
| current returns latest published frame, typed unavailable on failure | Done |
| Read requests never call next() on collector | Done |
| Multiple concurrent clients observe same sequence | Done (tested) |
| Backward-compatible stream over published frames with cursor | Done |
| Producer exhaustion does not kill Unix server | Done (tested) |
| Daemon serve starts producer before requests, stops after close | Done |
| Deterministic concurrency tests | Done |
| Repeated-current freshness test | Done |
| Two-client fan-out test | Done |
| History eviction/cursor test | Done |
| Startup failure test | Done |
| Shutdown test | Done |
| CLI attach tests | Covered by existing client tests |
| Update daemon/spec/readiness/status/measurements docs | Done |

## Out of Scope (preserved)

- Persistent disk history
- HTTP
- Mutation RPCs
- Peer authorization
- Changes to collector metric semantics
- P52 versioned bounded read contract
