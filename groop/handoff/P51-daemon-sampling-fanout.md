# P51 - Daemon-Owned Sampling And Fan-Out

## Goal

Make the daemon own one continuously advancing collector stream and serve
non-consuming snapshots/history to any number of clients. Requests must never
drive sampling cadence or make other clients see stale or distorted data.

## Workflow

- Branch: `feat/groop-p51-daemon-sampling-fanout`
- Worktree: `.worktrees/-groop-p51-daemon-sampling-fanout`
- Touch only `groop/**`; write P51-LOG.md/P51-REPORT.md; commit, do not merge.

## Requirements

- Refactor `FrameBroker` into a lifecycle with one background producer that
  advances the configured frame source independently of requests, stores a
  bounded sequenced history, and supports explicit start/stop/join.
- `current` returns the latest published frame and changes as sampling advances.
  Before the first frame it waits only for a bounded startup timeout and returns
  a typed unavailable error on failure or end-of-source.
- Read requests never call `next()` on the collector. Multiple concurrent
  clients observe the same sequence and cannot accelerate, consume, or starve
  each other.
- Define backward-compatible `stream` behavior over published frames; add
  sequence/cursor semantics if needed, with hard limits and bounded waits.
- Capture producer exhaustion and bounded collection errors without killing the
  Unix server; expose them to P47 health when available without depending on it.
- Daemon serve starts the producer before accepting useful requests and stops it
  deterministically after the server closes. No leaked threads on failure.
- Add deterministic concurrency, repeated-current freshness, two-client fan-out,
  history eviction/cursor, startup failure, shutdown, and CLI attach tests.
- Update daemon/spec/readiness/status/measurements docs.

## Out Of Scope

- Persistent disk history, HTTP, mutation RPCs, peer authorization, or changes
  to collector metric semantics.

