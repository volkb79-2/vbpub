---
kind: backlog-entry
schema_version: 1
id: CP-33
title: "Control-socket accept loop is serial: one slow peer or one long verb (stop-incomplete, report) blocks every other verb; watch threads are unbounded"
status: open
type: "bugfix"
severity: "medium"
component: "daemon"
context_estimate: "medium"
provenance: "Pre-release deep review of the 1.1.0 wave branch, 2026-10-27 (fable-cgprofile-20261027); lib/serve.py _accept_loop/_handle_connection"
filed_by: "Claude (cgprofile 1.1.0 pre-release review)"
spec_owner: "cgroup-profiler daemon"
priority: 2
filed_date: "2026-10-27"
---

## Observed (code reading, not a live incident)

`SessionServer._accept_loop` (`lib/serve.py`) accepts one connection at a time and runs `_handle_connection`
inline. Three things therefore serialise behind each other on the ONE control carrier that every run-gate lane shares:

1. The request-line read. A connected peer that sends bytes slowly, or nothing, holds the loop until the 25 s
   request deadline (`REQUEST_LINE_TIMEOUT_SECONDS`); the deadline is per connection and the loop is serial, so
   N such peers hold it N x 25 s. Any member of the `docker` group can open such a connection (the trust boundary
   is deliberate, D-30, but a buggy client is enough, no malice needed).
2. Long verbs. `stop` with a wedged worker now joins up to 10 s outside the daemon-wide lock and then may wait
   another 10 s on the session lock (CGP-110 B2/S9, `STOP_JOIN_SECONDS`); `report` runs the venv subprocess up to
   `report_timeout` (120 s). During that time `version`, `status`, `host` and every other lane's `start`/`stop`
   wait, and run-gate's own per-verb timeouts (contract §1.5) can expire against a healthy daemon.
3. `watch`. Each streaming connection gets its own daemon thread with no cap; a client that re-attaches in a
   loop grows the thread count without bound.

Bounded admission (16 control handlers, 32 watch streams, 32 request-line readers, `busy` refusal) is specified in
CP-24 D-33, but CP-24 is parked until after run-gate 24.x and is a large change. The serialisation above is
independent of the sampler-owned-stop redesign and can be fixed on its own.

## Required contract (interim, compatible with CP-24's later shape)

- Read request lines on a small bounded pool (or one thread per connection with a hard cap and a `busy` refusal
  after the cap), so a slow peer cannot stall the accept loop; keep the per-connection 25 s deadline and 1 MiB
  bound unchanged.
- Keep verb DISPATCH serialised where the code relies on it (the registry lock already protects state; document
  which verbs may run concurrently: `version`, `host`, `status`, `gc` are read-only snapshots and safe).
- Cap concurrent `watch` streams (CP-24's 32) and refuse beyond it with `busy` after the line is parsed.
- Protocol: `busy` is a new §8.8-style error code; additive, contract stays 1; document in `docs/PROTOCOL.md` and
  the mirrored run-gate contract copy.

## Oracles

- A test with one peer holding a half-written line for the full deadline while a second peer's `version` is answered
  within 1 s.
- A `stop` that returns `stop-incomplete` after 10 s while a concurrent `version` is answered within 1 s.
- The 33rd concurrent `watch` is refused with `busy`; the first 32 keep streaming.

## Folds / depends

Supersedes nothing; CP-24 absorbs it when it lands (its admission numbers are reused here on purpose).
