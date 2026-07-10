# P51 Report — Daemon-Owned Sampling And Fan-Out

**Branch:** `feat/groop-p51-daemon-sampling-fanout`
**Base:** `b5ba9af` (docs(groop): carve P51 daemon sampling fanout)
**Date:** 2026-07-10

## What Was Built

### `groop/src/groop/daemon/broker.py` — Refactored `FrameBroker`

Complete rewrite of the frame broker with a request-independent background
producer. Key changes:

1. **Background producer thread.** A single daemon thread continuously advances
   the frame source, publishing each frame into a bounded sequenced history.
   Read operations (`current`, `stream`) never call `next()` on the source.

2. **`start()`/`stop()`/`join()` lifecycle.** The producer starts explicitly (or
   lazily on first read), signals stop, and joins cleanly. Exceptions from the
   producer thread are captured and re-raised by `join()`.

3. **`current()` returns the latest published frame.** Before the first frame
   it waits for a bounded startup timeout (default 5 s) and raises
   `FrameUnavailableError` on timeout, source exhaustion, or producer failure.
   After the first frame it returns the most recent frame without blocking.

4. **`stream()` with sequence/cursor semantics.** Without a cursor, returns the
   tail of history (most recent *limit* frames). With a cursor, returns frames
   strictly after the cursor sequence number. Each response includes a `seq`
   field.

5. **Typed errors.** `FrameBrokerError` (base), `FrameUnavailableError` (no
   frame yet / exhausted / timeout), `FrameProducerError` (producer exception).

6. **Bounded history.** Configurable `history_size` (default 120), oldest frames
   are evicted via `deque(maxlen=...)`.

7. **Consecutive error tolerance.** The producer tolerates up to
   `source_error_limit` (default 5) consecutive exceptions before capturing the
   error.

### `groop/src/groop/cli.py` — Lifecycle Integration

The `daemon serve` subcommand now calls `broker.start()` before opening the
Unix server and `broker.stop()`/`broker.join()` in the finally block after
`server.server_close()`, ensuring deterministic producer teardown.

### `groop/src/groop/daemon/__init__.py` — Exports

`FrameBrokerError`, `FrameUnavailableError`, `FrameProducerError` exported.

### Tests — `groop/tests/test_daemon_p51.py`

21 new tests:

| Category | Tests | What They Cover |
|---|---|---|
| Lifecycle | 4 | Producer advances independently, repeated-current freshness, idempotent start, stop/join termination |
| Producer errors | 1 | join() re-raises captured exception |
| Startup / unavailable | 2 | Empty source timeout, never-yielding source timeout |
| Stream / cursor | 4 | Tail (no cursor), cursor, cursor beyond history, high limit |
| History eviction | 1 | Bounded deque evicts old frames |
| Concurrent fan-out | 2 | Two-client fan-out, concurrent current+stream |
| Protocol dispatch | 4 | current, stream (no cursor), stream (cursor), unknown op |
| Exhaustion | 2 | Current returns last frame after exhaustion, server stays alive |
| Shutdown | 1 | Clean teardown of broker + server + socket |

### Documentation

- **DAEMON.md**: Background producer section, protocol updated with cursor
  support, error types documented.
- **STATUS.md**: P51 marked as implemented instead of queued.
- **ARCHITECTURE.md**: "Daemon Producer & Fan-Out" section replaces P16 spike
  text; module map updated.

## Test Results

```text
$ PYTHONPATH=groop/src python3 -m pytest groop/tests -q
644 passed, 1 skipped, 1 warning in 51.39s
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
| Deterministic concurrency tests | Done (21 tests) |
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
