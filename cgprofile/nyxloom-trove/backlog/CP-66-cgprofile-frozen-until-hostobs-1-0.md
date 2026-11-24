---
kind: backlog-entry
schema_version: 1
id: CP-66
title: "cgprofile is frozen to fixes and the M4 mark forwarder until hostobs 1.0 replaces its collector and observer verbs"
status: open
type: "cleanup"
severity: "low"
component: "docs"
context_estimate: "small"
provenance: "hostobs design review 2026-11-24 §3 (M5 row); plan v4 §G"
filed_by: "vbpub maintenance"
spec_owner: "docs/design/hostobs/PLAN-v4-2026-11-08.md §G"
priority: 3
filed_date: "2026-11-24"
---

## Problem

hostobs replaces cgprofile's collector and its observer verbs. Until hostobs
1.0 runs on both hosts, cgprofile 3.x stays the production profiler on the
reinstalled hosts, and the boundary between the two tools is a written rule
(`hostobs/docs/CONSUMERS.md`, "Boundaries"; HOB-74), not an assumption.

## Wanted

- New collector and observer features route to hostobs; cgprofile takes fixes
  only, and shares no code with hostobs.
- At hostobs M4, `cgprofile mark` forwards each mark to hostobs as a point or
  span (through `hostobs_client` when it is present; otherwise it behaves as it
  does now).
- After hostobs 1.0 runs on both hosts with one R2 campaign each, cgprofile
  ships a final "retired" release; the hosts lose it at their next reinstall.
  No data moves.

## Acceptance criteria

- Every cgprofile change between now and the "retired" release is a fix or the
  M4 `mark` forwarder; anything else is filed against hostobs.
- The M4 `mark` forwarder lands as its own small package in cgprofile.
- The "retired" release states the retirement in its changelog entry.

## Dependencies and order

The forwarder waits for hostobs M4; the "retired" release waits for hostobs 1.0
on both hosts and one R2 campaign on each.
