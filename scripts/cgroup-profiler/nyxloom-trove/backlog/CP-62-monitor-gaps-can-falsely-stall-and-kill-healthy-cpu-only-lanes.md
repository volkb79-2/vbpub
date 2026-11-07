---
kind: backlog-entry
schema_version: 1
id: CP-62
title: "Monitor gaps can falsely stall and kill healthy CPU-only lanes"
status: fixed
type: "bugfix"
severity: "high"
component: "liveness"
context_estimate: "medium"
provenance: "telemetry-platform-DESIGN-v2.md §3, CGP-FS request 2026-11-07; CP-60 already occupied, so use next free id"
filed_by: "Codex implementation"
spec_owner: "docs/PROTOCOL.md §8.2/§8.4"
priority: 1
filed_date: "2026-11-07"
closed_date: "2026-11-07"
closed_reason: "CGP-FS passed cgroup-profiler r0-r1 (3418 passed, 100% line/branch coverage), r3 (6 canaries rejected), and Run-Gate selftest (3074 passed, 1 skipped, 100% diff line/branch coverage) on 2026-11-07"
---

## Observed mechanism and reproduction

`LivenessTracker` previously treated time between monitor readings as idle
time, then computed CPU activity from a trailing sampled window that lost the
CPU growth observed across a long sampler delay. A healthy CPU-only lane could
therefore be marked stalled and killed. Daemon throttling and a blocked or
stopped monitor could also leave the kill path without fresh evidence.

The finding and required fail-safe behavior are in
`/home/vscode/vbpub-release-program-20261027/reports/telemetry-platform-DESIGN-v2.md`
section 3 (FS-1 through FS-6).

## Why cgroup-profiler owns it

`cgprofiled` owns the monotonic liveness clock, exact cgroup reads, watch
verdicts, and the only authorized `cgroup.kill` write. A consumer wall timeout
and the daemon's authenticated-start `over_ceiling` check remain independent
backstops.

## Proposed contract

- Each liveness reading carries `seq`, `mono_ns`, `due_ns`, `fresh`, and its
  interval. A gap longer than `max(3 × interval, 5 s)` is `monitor-gap`, is
  added to `paused_for_seconds`, and does not advance idle or cadence time.
- CPU activity uses exact cumulative `cpu.stat usage_usec` deltas over true
  elapsed time, scaled to the 30-second rule. `io.stat` bytes and stream
  identity are compared across gaps.
- Daemon-cgroup throttling or a non-fresh confirmation reading yields a
  reported verdict with `monitor_state: degraded-monitor`; it cannot request
  a kill. An absent or stalled monitor yields state `unknown` and no kill.
- Every kill retains the authenticated `on_stall=kill` grant and exact cgroup
  boundary. `stalled`/`hung` require three consecutive fresh kill-state
  readings spanning at least 60 seconds and immediate pre-write no-growth
  confirmation against a reading at least 30 seconds old. `over_ceiling` can
  stop active work on one fresh healthy reading after the daemon-clock ceiling
  is passed; it does not wait for stall evidence or no-growth. `runaway` below
  the ceiling is report-only.
- Missing or empty `io.stat` is zero observed I/O; a read error refuses the
  confirmation. A configured progress stream has a 120-second creation grace;
  before it expires confirmation is transiently refused, then stable absence
  is treated as no stream growth.
- Transient confirmation failures clear kill evidence so the state can be
  evaluated again; definitive counter unavailability and exact-boundary
  refusals remain terminal and report their reason.
- A session found live after daemon restart is finalized with manifest reason
  `daemon-restarted`, or `daemon-restarted-placement-recovery-failed` when
  placement restoration fails; liveness clocks are never resumed.

## Oracles

- A CPU-only lane active during a monitor gap is not marked stalled; the gap
  is paused and the cumulative CPU delta survives into the 30-second rule.
- Fake-clock monitor faults (stopped monitor, increased daemon throttling,
  stale or blocked reads) never write `cgroup.kill`.
- At actual sample cadences of 0.25, 1, 2, 5, and 30 seconds, a stalled target
  retains the full evidence window and reaches the serve kill boundary.
- A busy over-ceiling lane is killed with a fresh monitor, while a busy silent
  lane below its ceiling is not; one fresh sample is sufficient once its
  daemon-clock ceiling has passed.
- A stable stalled/hung target requires three fresh readings over 60 seconds
  and a final exact counter/stream confirmation at least 30 seconds after its
  earlier reading; any growth refuses the kill and can be reconsidered later.
- An empty or missing `io.stat` confirms zero growth; an existing-file read
  error refuses. A never-created configured stream refuses before its 120 s
  grace and is treated as stable absence after the grace.
- An enforced kill remains `killed` in tracker, status, Summary and stream,
  including after a later monitor gap.
- Protocol fields and values are documented in `docs/PROTOCOL.md` and both
  byte-identical copies of `RG55-INTERFACE-CONTRACT.md`. README, DESIGN-GUIDE,
  and CONSUMERS describe what the capability does, why, and how to consume it.
- Run-Gate accepts the additive `unknown` state, continues consuming the
  stream, and does not emit a stalled warning for that informational state
  (RG-245).

## SPEC ownership

`docs/PROTOCOL.md` §8.2 and the canonical Run-Gate interface contract §8.2/§8.4.

## Updates
