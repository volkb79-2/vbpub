# P51 Work Log

## Context

- Branch: `feat/groop-p51-daemon-sampling-fanout`
- Worktree: `.worktrees/-groop-p51-daemon-sampling-fanout`
- Base commit: `b5ba9af` (docs(groop): carve P51 daemon sampling fanout)
- Package: P51 — Daemon-Owned Sampling And Fan-Out
- Current objective: Refactor FrameBroker into a lifecycle with one request-independent background producer, bounded sequenced history, non-consuming current/stream fan-out, and sequence/cursor semantics.

## Timeline

```text
2026-07-10 ~18:00 UTC
- Action: Read handoff, explore codebase structure, read FrameBroker, CLI, tests, docs.
- Files changed: (research phase)
- Result: Full understanding of existing codebase architecture. FrameBroker was pull-based — current() and stream() both called next() on the source.

2026-07-10 ~18:15 UTC
- Action: Rewrite groop/src/groop/daemon/broker.py with background producer, bounded sequenced history, start()/stop()/join() lifecycle, Condition-based notification, lazy auto-start.
- Files changed: groop/src/groop/daemon/broker.py
- Result: FrameBroker now has one background daemon thread that continuously advances the frame source. current() returns the latest published frame and blocks briefly on first call for the startup timeout. stream() reads from published history with optional cursor parameter. Added FrameBrokerError, FrameUnavailableError, FrameProducerError typed exceptions.

2026-07-10 ~18:25 UTC
- Action: Fix backward compatibility — added lazy start to current() and stream() so existing callers that don't call start() still work.
- Result: Existing tests continue to pass.

2026-07-10 ~18:30 UTC
- Action: Update CLI daemon serve (groop/src/groop/cli.py) to call broker.start() before serving and broker.stop()/broker.join() after server_close() in the finally block.
- Files changed: groop/src/groop/cli.py
- Result: Daemon lifecycle integrated with broker lifecycle.

2026-07-10 ~18:35 UTC
- Action: Export new error types from daemon/__init__.py.
- Files changed: groop/src/groop/daemon/__init__.py
- Result: Public API surface updated.

2026-07-10 ~18:40 UTC
- Action: Update existing tests to match new semantics (current returns latest, stream returns tail).
- Files changed: groop/tests/test_daemon_broker.py, groop/tests/test_daemon_client.py
- Result: Existing tests pass with updated assertions.

2026-07-10 ~19:00 UTC
- Action: Write comprehensive P51 tests (21 tests) covering:
  - Producer advances independently
  - Repeated current() freshness
  - Idempotent start
  - Stop/join termination
  - Producer error propagation via join()
  - Empty source timeout
  - Never-yielding source timeout
  - Stream tail (no cursor)
  - Stream with cursor
  - Cursor beyond history
  - High limit
  - History eviction (bounded)
  - Two-client fan-out
  - Concurrent current + stream
  - Backward-compatible responses dispatch
  - Stream without cursor via responses
  - Stream with cursor via responses
  - Unknown op error
  - Current after exhaustion (returns last frame from history)
  - Exhaustion does not crash server
  - Shutdown cleanup
- Files changed: groop/tests/test_daemon_p51.py
- Result: All 21 tests pass in 3.8s.

2026-07-10 ~19:10 UTC
- Action: Update documentation.
- Files changed: groop/docs/DAEMON.md, groop/docs/STATUS.md, groop/docs/ARCHITECTURE.md
- Result: Docs updated to reflect background producer, fan-out, cursor semantics.

2026-07-10 ~19:15 UTC
- Action: Run full test suite.
- Result: 644 passed, 1 skipped (up from 623+21 = 644, correct).

2026-07-10 ~19:20 UTC
- Action: Run py_compile on all changed files.
- Result: All 6 files compile clean.
```

## Files Changed

| File | Change |
|---|---|
| `groop/src/groop/daemon/broker.py` | Full rewrite: background producer, lifecycle, cursor, typed errors |
| `groop/src/groop/daemon/__init__.py` | Export new error types |
| `groop/src/groop/cli.py` | broker.start()/stop()/join() in daemon serve |
| `groop/tests/test_daemon_p51.py` | 21 new P51-focused tests |
| `groop/tests/test_daemon_broker.py` | Update assertions for new semantics |
| `groop/tests/test_daemon_client.py` | Update assertions for new semantics |
| `groop/docs/DAEMON.md` | Background producer, protocol update, cursor docs |
| `groop/docs/STATUS.md` | P51 implemented, not queued |
| `groop/docs/ARCHITECTURE.md` | Daemon producer & fan-out section, module map update |

## Decisions

1. **Lazy start**: `current()` and `stream()` auto-start the producer if `start()` hasn't been called. This keeps backward compatibility with existing callers (tests, attach client).
2. **Bounded startup timeout**: default 5 s before `FrameUnavailableError`. Short enough for tests, long enough for real collectors.
3. **Exhaustion preserves history**: after source exhaustion, `current()` returns the last frame from history rather than raising an error. This prevents unnecessary client failures.
4. **Terminal errors fail closed**: an iterator exception terminates that
   iterator and is recorded once as a persistent public-safe typed failure; no
   fictional retry counter is claimed.

## Next Steps

- P52: versioned bounded read contract for production web backend.

## Controller Correction

- Merged P47/current main and restored strict `health-v1` behavior.
- Replaced racy startup, discarded errors, silent live-thread join, replaying
  polling, and leaking socket tests with atomic lifecycle, persistent typed
  terminal state, cursor/gap batches, bounded clients/requests, interruptible
  production sleep, and warnings-as-errors coverage.
- Focused daemon/client/health/record gate: `90 passed in 16.84s`.
- Full gate: `692 passed, 1 skipped in 53.29s` with `-W error`.
