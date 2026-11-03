---
kind: backlog-entry
schema_version: 1
id: CP-40
title: "Ancestor local memory.events deltas (up to dev.slice) in the Summary and in contract-3 window context, so consumers can tell ancestor-driven reclaim from a lane's own limit"
status: open
type: "feature"
component: "summary"
context_estimate: "small"
provenance: "Operator decision 2026-11-03 (run-gate: time stalled on memory is a contamination flag, not a lane property); cross-tool design docs/design/RESOURCE-PROFILES.md sec 6.1; adversarial self-review of that design (host-wide PSI is not a usable criterion on the shared host)"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler daemon"
priority: 2
filed_date: "2026-11-03"
---

## Problem

Run-gate must classify a lane's memory stall:

- caused by the lane's own limit, which is a capacity signal;
- caused by reclaim from above, which is contamination.

The Summary (schema 1) carries the lane leaf's `events`, from `memory.events.local`, and `host.slice` (the stall and
peak of the slice above the target). It does not carry the **local** `memory.events` deltas of the ancestors.

- The host-wide `/proc/pressure/memory` figures are not a usable substitute. On the shared host, the production
  service's own swapping keeps them high. Any threshold on them would flag every run and leave run-gate without a
  sizing basis.
- An ancestor's local `high` or `max` event during the window is direct evidence that reclaim was driven from above
  the lane.

## Design

- **Summary.** An additive optional key, `ancestors_memory_events_local`: a list from the target's parent up to and
  including the configured root of the dev tree (default `dev.slice`; configurable with CP-36). Each entry is
  `{path, high, max, oom, oom_kill}` as deltas over the session. An unreadable file gives `null` for that entry.
  - This is the same reader as the contract-2 observer's ancestry walk.
  - The Summary schema stays v1.1 (optional keys, like `liveness` and `placement`).
- **Contract-3 window context (CP-39).** The same list, as deltas over the window.

## Acceptance criteria

- On a fake cgroupfs, a `high` event written at `dev-gates.slice` during the session appears as `high: 1` on that
  entry, and nowhere else.
- An event at the lane leaf appears only in `events`, not in the ancestors list.
- An unreadable ancestor file gives `null` for that entry.
- The golden `summary-v1.json` without the key still validates. The new golden fixture, with the key, is vendored
  into run-gate.

## Test plan

Unit tests on the fake tree; the vendored fixture test in run-gate (RG-144).

## Dependencies and order

Ships in cgprofile 1.2.0 together with CP-39. Run-gate RG-144 uses it. Until then the ancestor criterion is skipped
and recorded as `ancestor_events: unknown`: it neither flags nor clears a run.
