---
kind: backlog-entry
schema_version: 1
id: CP-20
title: "Resource observer must expose local and hierarchical event counters for R2 attribution"
status: carved
type: "bugfix"
severity: "high"
component: "resource-observer"
context_estimate: "medium"
provenance: "Assay B155 implementation, 2026-10-11: hierarchical ancestor counters conflate sibling events with shared-parent enforcement"
filed_by: "vbpub maintenance"
spec_owner: "cgroup-profiler resource observer"
priority: 1
filed_date: "2026-10-11"
---

## Problem

The native R2 resource observer returns `pids.events` and `memory.events` for the candidate and every ancestor. Those files are hierarchical by default. When another leaf cgroup hits its own limit, the event can appear in the observer's shared ancestor sample even though the Assay candidate was unaffected. Assay must then reject the candidate as ambiguous. This prevents Assay from distinguishing a sibling-only event from an event enforced at the shared ancestor.

## Expected behavior

Add a versioned resource-observer response that exposes explicit scope metadata and local counters (`pids.events.local`, `memory.events.local`) alongside regular event counters for every visible cgroup. Preserve the existing peer authentication, complete ancestry, mount identity, controller and limit facts, and fail-closed checks. The observer must refuse when a controller is active but required scope evidence is unavailable or unstable. `memory_localevents` may supply equivalent local memory evidence, subject to the candidate-subgroup check. `pids_localevents` must be refused: it counts failed forks in the cgroup where the fork was attempted, even when an ancestor's `pids.max` caused the failure, and therefore cannot prove which cgroup enforced the limit.

The API must remain explicit about its contract version. A consumer must not silently interpret contract-1 hierarchical counters as local counters. Controller ruling RW-569 (2026-10-12): retire observer contract 1 before the contract-2 release; return a clear unsupported-contract refusal. The minimum daemon version is cgroup-profiler 1.0.0, the combined RG-55 first release. The daemon's separate control protocol remains contract 1.

## Acceptance oracles

- Contract tests prove the new request/response version and exact scope metadata and local-counter fields; malformed, missing, unstable, or unknown scope data is refused.
- Contract-1 observer requests receive a `bad-argument` response with an explicit unsupported-contract message; the separate control protocol remains contract 1.
- Fake cgroup trees prove `pids.events.local` and `memory.events.local` are read from the matching cgroup, while hierarchical files remain distinct and are not substituted silently.
- A sibling-only leaf event changes a shared hierarchical ancestor counter but not that ancestor's local event counter.
- An event enforced at the shared ancestor changes that ancestor's local counter.
- `pids_localevents` is refused with a diagnostic that explains why its fork-failure counter cannot attribute the enforcing cgroup.
- `memory_localevents` is represented; child-cgroup refusal applies when a candidate-local memory event file is active.
- Peer binding, gate-slice membership, mount/ancestry validation, and read-only behavior remain covered by the registered unit, coverage, and canary gates.
- `scripts/cgroup-profiler/README.md`, `docs/DESIGN-GUIDE.md`, `docs/CONSUMERS.md`, and protocol/interface-contract documentation explain the new contract and compatibility requirements.

## Updates

**2026-10-13** — 2026-10-13: Contract-2 local/hierarchical event implementation is present in the release candidate. Exact-tip registered gates and live observer acceptance remain pending.

**2026-10-13** — 2026-10-13: The contract-2 observer implementation and scope tests are present in the release candidate. Keep this entry carved until the exact-tip registered gates and live observer acceptance pass; the current in-progress tree is not yet release evidence.

**2026-10-15** — An independent review found that `pids_localevents` cannot substitute for `pids.events.local` in native R2 attribution: its local fork-failure counter does not identify which ancestor's `pids.max` rejected the fork. The observer now refuses this mount mode; the exact-tip gates and live observer acceptance remain pending.
