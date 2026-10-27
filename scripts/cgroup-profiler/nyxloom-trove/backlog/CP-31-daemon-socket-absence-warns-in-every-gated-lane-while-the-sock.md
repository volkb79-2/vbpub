---
kind: backlog-entry
schema_version: 1
id: CP-31
title: "Daemon socket absence makes profiling warn in lanes; classify the causes and keep a missing socket distinct from a refusing daemon"
status: open
type: "bugfix"
severity: "low"
component: "daemon"
context_estimate: "small"
provenance: "Release program notes 2026-10-2x ('the cgprofile daemon socket is absent: profiling warns in every lane'), re-measured 2026-10-27"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler daemon"
priority: 3
filed_date: "2026-10-27"
---

## Observed

During the earlier release program, run-gate lanes warned that the cgprofile daemon socket was absent and fell back to basic
profiling. Re-measured on 2026-10-27: the host daemon container is up with its control socket present, and a registered r0-r1
lane started a profile session "via socket" with no warning. So the absence is not a standing condition of the daemon; it appears
when the lane's environment does not have the socket mounted (a nested or isolated gate container, a sandboxed tester) or while the
daemon is down.

## Risk specific to 1.1.0

`cgprofile serve` now refuses to start unless it runs under a bounded `cgprofile.slice` (Sol B4). On a host without the loaded slice unit
(or with an unbounded one), the container restarts forever (`restart: unless-stopped`) and the socket is absent for every lane,
which looks exactly like the warning above. Measured 2026-10-27: this host has `cgprofile.slice` loaded with `MemoryMax=1073741824`,
so the 1.1.0 daemon passes its own guard here. Consumers cannot tell "socket not mounted" from "daemon crash-looping on the slice guard".

## Required contract

- `cgprofile ctl version`/doctor and the run-gate warning text should name which of the three causes applies (socket path not present
  in this environment, daemon container not running, daemon refusing to start with the slice reason), using only what the carrier can see.
- Document the three causes next to the unit-install step in `docs/CONSUMERS.md`.
- CIU-150 (Docker-host slice proof before container creation) removes the crash-loop cause.

## Oracles

Fixture per cause produces a distinct, actionable message; no cause is reported as "absent" when the socket exists.
