---
kind: backlog-entry
schema_version: 1
id: CP-23
title: "Per-candidate cgroups for safe parallel R2 attribution"
status: open
type: "feature"
component: "resource-observer"
context_estimate: "large"
provenance: "Claude Opus 5.5 high systems review, 2026-10-13; Assay B156/A-491 and B110 pilot constraints"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler resource observer"
priority: 1
filed_date: "2026-10-13"
---

## Problem

Contract 2 distinguishes candidate-local events from local events at relevant ancestors. Assay serializes native R2 candidates because candidates in one shared lane cgroup would otherwise share overlapping before/after counter windows. This is correct for attribution, but it limits throughput: the B110 local pilot uses one worker, and long mutation campaigns occupy one gate slot for many hours.

Contract 2 must continue to fail closed when a shared ancestor's own limit event could have affected a candidate. A sibling leaf reaching only its own limit should not invalidate unrelated candidates, provided each candidate's local event window is isolated.

## Future contract

Design a versioned observer capability that can create and own a disposable cgroup leaf per candidate, bind it to one authenticated tester container and candidate token, place only the verified candidate process tree there, return that leaf's local pids.events and memory.events, and remove the leaf only after it is empty. Continue observing relevant ancestor-local counters: an event at a shared ancestor remains a lane-level ambiguity, while a sibling-only leaf event does not contaminate another candidate.

The design must compare this with other deployment choices, including placing the whole tester container closer to the host root. Preserve support for workloads running inside a declared slice tree when that remains safe; root placement does not itself prove that no higher-level limit exists.

## Constraints to investigate

- The tester's cgroup mount may be read-only, so placement may need a narrowly authenticated host-side daemon operation.
- Cgroup v2's no-internal-process rule affects where the candidate runner and its children can live.
- The daemon must bind authorization to the container identity, candidate token, process start identity, and exact delegated scope. It must not accept arbitrary paths, PIDs, or cgroup moves.
- Candidate startup, migration, cleanup, and event reads must form one bounded lifecycle. Every refusal or cleanup error yields no candidate outcome.
- Measure the added per-candidate cost and verify that the observer still detects a limit enforced at a shared ancestor.

These are design questions, not claims that contract 3 is already feasible.

## Acceptance oracles

- A sibling candidate hitting only its leaf's pids.max or memory.max does not invalidate another candidate's event window.
- A candidate hitting its own leaf limit is classified as a candidate resource failure, never a functional kill.
- An event enforced at a shared ancestor invalidates the affected lane or batch conservatively.
- PID reuse, a moved candidate, a dead container, a stale token, a symlink, an occupied leaf, and incomplete cleanup are refused without writing a verdict for the candidate.
- Exact-tree gates exercise parallel candidates in a nested slice tree and prove no event is lost between the candidate leaf and relevant ancestors.
- A measured pilot compares serialized contract-2 execution, per-candidate leaves, and a more root-level container placement for attribution, throughput, memory, and operational cost.
- README, DESIGN-GUIDE, CONSUMERS, protocol documentation, and the compatibility matrix describe any eventual public contract before release.
