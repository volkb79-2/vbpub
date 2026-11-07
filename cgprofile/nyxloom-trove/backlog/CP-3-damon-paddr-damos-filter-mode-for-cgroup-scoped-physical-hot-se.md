---
kind: backlog-entry
schema_version: 1
id: CP-3
title: "DAMON paddr/DAMOS-filter mode for cgroup-scoped physical hot-set (vaddr per-pid is v1)"
status: open
type: "feature"
severity: "low"
provenance: "RG-55 wave plan sec5 P0, D-3, 2026-09-12"
filed_date: "2026-09-12"
---

## Observed mechanism and reproduction

RG-55 plan D-3 turns DAMON hot/warm/cold classification ON by default for
every profiled lane, using per-pid **vaddr** targets (the lane's pid
subtree, contract §4.3/§4.4) — v1's only supported mode. DAMON also
supports a **paddr** (physical address space) mode with **DAMOS filters**
that can scope the monitored region to a cgroup directly, rather than to a
set of pids resolved via `/proc/<pid>/environ` scanning and periodic
re-discovery. paddr+DAMOS-filter is a materially different attribution
mechanism: it would classify hot/warm/cold sets for whatever physical pages
a cgroup's processes currently hold, independent of tracking which pids are
in scope at each discovery interval — potentially more robust for a lane
that spawns short-lived children faster than the discovery interval catches
them (a known v1 gap: "a lane that spawns detached daemons outside the
subtree is under-attributed", RG-55 plan §10 risk).

## Why cgroup-profiler owns it

DAMON mode selection, kdamond configuration and target-type (vaddr vs
paddr) are entirely internal to the daemon's DAMON multiplexing
(`lib/damon.py` `DamonSession`, contract §4.4); no consumer (run-gate)
declares or needs to know which underlying DAMON mode produced the
hot/warm/cold numbers, only the classified bytes themselves (contract §3
`damon.*_bytes`).

## Proposed contract

Not designed here. Sketch: an opt-in daemon-level (or per-session) mode
switch, `--damon-mode vaddr|paddr` (default `vaddr`, preserving v1
behavior unchanged), where `paddr` mode uses a DAMOS filter scoped to the
target cgroup's physical pages instead of the vaddr pid-subtree scan.
Requires measuring whether paddr mode is actually more accurate for the
cgroup-scoped case in practice (it trades pid-attribution precision for
cgroup-membership precision — a paddr filter would not distinguish TWO
lanes sharing one container the way per-pid vaddr targets currently do
under `container-shared` scope, so it may be strictly WORSE for exec-mode
attribution even if it is better for ephemeral single-lane containers).
This needs a real comparison against v1's measured overhead and accuracy
(RG-55 plan §10: "DAMON per-kdamond overhead unknown on this host →
measured in P1/P3") before it is worth building.

## Oracles

Not yet written — needs the mode comparison above first, plus a fake-sysfs
DAMOS-filter test analogous to the vaddr multiplex test RG-55 P1 already
writes (contract §4.4, "a fake-sysfs DAMON multiplex test").

## SPEC ownership

`scripts/cgroup-profiler/DESIGN.md`, `lib/damon.py`, RG-55 contract §4.4
(DAMON multiplexing — a mode addition would extend this section, not
replace it).

## Provenance

RG-55 wave plan §5 P0, D-3 (DAMON hot/warm/cold default-on scope) and §1
(v1 vaddr-per-pid architecture as currently the only mode), filed by the P0
controller package, 2026-09-12.

## Updates
