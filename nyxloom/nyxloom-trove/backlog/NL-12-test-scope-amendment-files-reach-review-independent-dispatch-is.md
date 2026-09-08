---
kind: backlog-entry
schema_version: 1
id: NL-12
title: "test_scope_amendment_files_reach_review_independent_dispatch is timing-sensitive under heavy host load, failed once with a spurious REVIEW_REJECTED"
status: open
type: "bugfix"
severity: "low"
component: "testing"
provenance: "nyxloom-P103 post-merge gate verification, 2026-09-08"
filed_date: "2026-09-08"
---

## Observed mechanism and reproduction

`tests/test_behavioral.py::test_scope_amendment_files_reach_review_independent_dispatch`
failed once under heavy host load (2026-09-08, nyxloom-P103's post-merge
gate verification — the host was concurrently running many other
sessions' containers, load average 9-11 on 8 cores, and the harness's own
low-memory guard was killing unrelated wrapper processes at the time):

```
AssertionError: the review dispatch must have actually run (and approved)
for this assertion to mean anything about LaunchReview's amendment
aggregation; got TaskState.REVIEW_REJECTED
...
notes='review verdict: missing (receipt: done) -- no typed result for
this attempt'
```

The test drives a real `Daemon` through `for _ in range(30): _tick(d,
"demo")`, waiting for a `FakeScript`-scripted review step to reach a
terminal state. Re-running the IDENTICAL commit (`66fde6ce`) immediately
afterward, once host load had dropped (load average ~5, more available
memory), passed clean on the full 782-test suite with zero changes to
anything. nyxloom-P103 (the package under test at the time) changes ZERO
Python, so this cannot be a regression it introduced — it is a
timing-sensitive interaction between the daemon's tick loop and the fake
review script's "typed result" recording that a sufficiently loaded host
can perturb.

## Why nyxloom owns it

This is nyxloom's own test suite and its own `FakeScript`/daemon-tick
harness (`tests/test_behavioral.py`, `nyxloom.testing.FakeScript`) —not an
external tool's defect.

## Proposed contract

Investigate why "review verdict: missing (receipt: done) -- no typed
result for this attempt" can occur at all for a scripted, deterministic
fake review step (`_review_approve_step`) — under normal conditions the
fake CLI's queued step should always produce a typed result by the time
the daemon's tick observes it. Two candidate directions, not mutually
exclusive: (a) the test's own polling loop (`for _ in range(30):
_tick(...)`) may need a longer or load-aware bound, or an explicit wait
for the fake process's completion rather than relying on wall-clock ticks
alone; (b) there may be a genuine race in how the daemon or the fake CLI
harness records a "typed result" for an attempt, which host load merely
makes visible rather than causes — this is the more concerning
possibility and should be ruled out first. Do NOT simply widen the retry
loop as a blind fix without first determining which of the two applies;
a raced result silently swallowed by a wider loop is worse than an
occasional loud failure.

## Oracles

- The failure mode ("no typed result for this attempt") reproduces
  reliably under a controlled resource-constrained environment (e.g. a
  cgroup-capped CPU/memory budget for the test process), confirming it is
  load-sensitive and not a one-off fluke, OR is shown to be a genuine race
  independent of load (e.g. reproducible via a deliberately-delayed fake
  CLI response).
- Whatever fix lands must not weaken the assertion the test exists to
  prove (`LaunchReview`'s amendment aggregation actually runs and
  approves) — a fix that simply lengthens timeouts without addressing a
  real race would mask a genuine daemon bug.

## SPEC ownership

`tests/test_behavioral.py`, `src/nyxloom/testing.py` (`FakeScript`),
whichever daemon/effects module actually records an attempt's "typed
result" (worth identifying precisely during investigation, not assumed
here).

## Provenance

Found during nyxloom-P103's post-merge gate verification, 2026-09-08 —
different flaky test than the already-tracked B25
(`test_transient_throttle_resumes_same_attempt_end_to_end`, a real
`os.fork()` double-fork flake); this one is a scripted-review timing
issue, not a fork issue. Filed separately since the mechanisms are
unrelated.
