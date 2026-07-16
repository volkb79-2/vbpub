# P51 Optimized Benchmark — Daemon-Owned Sampling And Fan-Out

## Purpose

This is a benchmark refinement of `P51-daemon-sampling-fanout.md`, not a new
product package. Implement the same feature from the same historical base. The
extra detail makes implicit production invariants and acceptance oracles
explicit without prescribing a particular class or file layout.

## Workflow

- Work only in the assigned historical-base worktree and branch.
- Touch only `topos/**`; do not inspect other worktrees or later commits.
- Read only the minimum relevant context first: `topos/README.md`, this handoff,
  `topos/CONTRACTS.md`, the current broker/client/daemon-serve code, their tests,
  and the daemon/spec/readiness/status/measurements documents that must change.
- Write `topos/handoff/reports/P51-LOG.md` and `P51-REPORT.md`, run every gate,
  commit, and do not merge.

## Product Goal

The daemon owns exactly one continuously advancing collector stream. Any number
of clients read the same latest frame or bounded history without driving the
collector, consuming frames, changing cadence, or starving other clients.

## Required Contracts

### Producer ownership and lifecycle

- Only the producer thread may call `next()` on the source. No read, protocol,
  client, status, or health path may advance it.
- Concurrent `start()` calls create at most one producer. Define and test the
  lifecycle after exhaustion, failure, and stop; state must describe the real
  thread rather than a requested state.
- `current` waits only for a bounded first-frame deadline. Exhaustion, failure,
  explicit stop, startup timeout, and shutdown timeout are distinct persistent,
  typed outcomes.
- Shutdown must never claim success while the producer is alive. An arbitrary
  iterator may block forever in `next()`; support an interrupt callback/event
  for production sources and return or raise a typed bounded-timeout outcome
  when interruption is impossible.
- After stop is requested, a source released from `next()` must not publish a
  new frame. Recheck stop before publication.
- Make the production live-stream interval interruptible; do not wait an entire
  sampling interval during normal shutdown.
- In daemon serve, finish collector/provider configuration before starting the
  producer. Socket construction, provider initialization, serve failure, and
  normal close must not leak a producer or race collection against provider
  mutation.

### Atomic publication and history

- Publish `(sequence, frame)` atomically. A `current` response must never pair
  frame N with sequence N+1.
- Sequence numbers are monotonic and attached to every wire frame.
- History has a validated positive capacity and bounded read limit. Tail reads
  preserve backward-compatible non-consuming behavior.
- Cursor reads return only frames newer than the cursor. If eviction makes the
  requested continuation incomplete, return explicit machine-readable gap,
  oldest/latest, and next-cursor metadata. The client must not silently skip or
  replay frames; its polling helper must advance a cursor and surface a gap.

### Failure safety and resource bounds

- Never expose raw producer exceptions, secrets, filesystem paths, or arbitrary
  exception text over the socket or health surface. Preserve only a safe typed
  code/message; retain the original exception privately if useful for chaining.
- Keep the Unix server alive after producer exhaustion/failure so health and the
  last valid frame remain queryable.
- Strictly validate request shape and types. Reject booleans as integers,
  non-finite timeouts, unknown fields, invalid cursors, and out-of-range limits;
  do not silently clamp malformed requests.
- Bound request bytes, request-read time, concurrent clients/handler threads,
  wait duration, response frame count, response line size, and total response
  bytes. A slow or abandoned client must not consume unbounded resources.
- Preserve existing read-only operations and reject unsupported/mutation-shaped
  operations.
- Integrate producer state into P47 health only when that capability exists on
  the historical base; do not invent a dependency on absent code.

## Required Deterministic Tests

Use events, barriers, queues, and bounded polling rather than sleeps as the
primary synchronization mechanism. Tests must fail against the pre-P51 broker
and must cover at least:

1. request-independent advancement and repeated-current freshness;
2. concurrent start creates one producer;
3. two clients observe the same sequence without advancing the source;
4. atomic frame/sequence response under concurrent publication;
5. blocked startup timeout, empty source, failure before first frame, failure
   after a valid frame, and persistent terminal state;
6. interruptible production sleep and uninterruptible-source typed join timeout;
7. release-after-stop cannot publish;
8. bounded eviction with explicit stale-cursor gap metadata;
9. polling client does not replay a retained tail and surfaces eviction gaps;
10. raw `TOKEN=secret /private/path` producer text never reaches protocol or
    health output;
11. strict malformed request validation, oversized/unterminated request,
    slow-client timeout, and concurrent-client rejection;
12. socket creation/serve failure and normal close leave no producer or handler
    threads; CLI attach/current regressions remain green.

Do not weaken or merely rewrite old expectations to make the implementation
pass. Assert observable contracts, including actual thread liveness after
shutdown.

## Documentation and Evidence

Update every surface required by the original handoff: daemon documentation,
architecture/spec, readiness, status/roadmap, measurements, and package reports.
Do not claim a gate that was not run. Record environment limitations separately
from implementation failures.

Run:

```bash
PYTHONPATH=topos/src <project-python> -m pytest <focused P51/daemon tests> -q -W error
PYTHONPATH=topos/src <project-python> -m pytest topos/tests -q -W error
<project-python> -m py_compile <all Python files under topos/src/topos and topos/tests>
git diff --check
```

## Out Of Scope

Persistent disk history, HTTP, mutation RPCs, peer authorization, and collector
metric-semantic changes remain out of scope.
