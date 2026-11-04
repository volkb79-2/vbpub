---
kind: backlog-entry
schema_version: 1
id: CP-33
title: "Control-socket accept loop is serial: one slow peer or one long verb (stop-incomplete, report) blocks every other verb; watch threads are unbounded"
status: fixed
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

FIXED 2026-11-04 (05ea66bda). `_accept_loop` hands every connection to its own thread (`_admit_connection`); a reader slot (32) covers only the request-line read, a handler slot (16) wraps `_dispatch` (`_dispatch_admitted`), a watch slot (32) is held for the life of a stream; a connection over a cap, or one the OS cannot give a thread, is answered `busy` (`_refuse_busy`, bounded 0.2 s send). Verb dispatch is concurrent: the registry RLock and per-session locks already guard all shared state (`version`/`host`/`status`/`gc` are read-only snapshots). Shutdown sets `_stopping`, waits up to 12 s for in-flight verbs, then finalizes sessions, so a late `start` cannot land after finalization. `busy` is documented in `docs/PROTOCOL.md` section 6, `docs/CONSUMERS.md` and both byte-identical copies of the run-gate contract. Tests: `test_cp33_*` in `tests/test_serve.py` (stalled peer does not delay `version`; a long `host` verb does not delay `version`; handler cap answers `busy` and returns its slot; failing handler returns its slot; reader cap; watch cap and slot return; thread-start failure for both paths; shutdown drain and its bound). Delta review 2026-11-04 (fix-cgp-20261104c): `report` now has its own pool of 2 (`MAX_REPORT_JOBS`, separate from the 16 shared handler slots), and the shutdown drain is 4 s (join 4 s, forced finalize lock wait 1 s) so it fits docker's 10 s stop grace; the ciu template sets `stop_grace_period: 30s`. Note: the oracle "a `stop` that returns `stop-incomplete` after 10 s while `version` is answered within 1 s" is covered by the long-verb test with a blocking dispatch stand-in rather than a real 10 s wait.
