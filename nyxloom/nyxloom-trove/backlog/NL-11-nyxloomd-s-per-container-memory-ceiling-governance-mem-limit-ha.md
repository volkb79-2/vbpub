---
kind: backlog-entry
schema_version: 1
id: NL-11
title: "nyxloomd's per-container memory ceiling (governance.mem_limit) has never been measured; 6g is an interim estimate"
status: open
type: "bugfix"
severity: "medium"
component: "nyxloomd"
provenance: "nyxloom-P103, Work item 9, 2026-09-08"
filed_date: "2026-09-08"
---

## Observed mechanism and reproduction

`nyxloomd`'s per-container memory ceiling (`[nyxloomd.governance] mem_limit`,
added by nyxloom-P103 at `6g`) has never been measured against real
behaviour. Two facts in the monorepo already flag this as unresolved:

- `infra/slices/nyxloom-agents.slice:13-16` leaves the agent-session memory
  ceiling blank, annotated *"OPERATOR MUST SET"*.
- `infra/slices/nyxloom-daemon.slice:33-37` ships `MemoryMin=128M` as an
  avowedly conservative floor, with its own "TO REFINE" note prescribing the
  method: run the daemon idle, then under a real dispatch wave, and read
  `memory.current` plus `memory.stat`'s `anon` field at both points.

nyxloom-P103 derived `6g` analytically, not from measurement: `routes.host.toml`
declares 8 routes with `trust = "operator"`, which `containment.py:204-213`
runs UNCONTAINED as direct children of the daemon process, and
`nyxloom-trove/nyxloom.toml`'s `max_active_tasks = 5` bounds how many can run
concurrently — so up to 5 full agent CLIs are charged to `nyxloomd`'s own
memory cgroup under normal operation, in addition to the daemon itself
(~256M estimated). `6g` ≈ 256M + 5 × ~1g, rounded up, where the ~1g-per-CLI
figure is itself an unverified estimate, not a measurement.

Reproduction: `docker stats` history is unavailable for `nyxloomd` (it is not
currently running under ciu governance in this checkout), and no nyxloom doc
records an observed RSS/anon figure for either the idle daemon or a live
dispatch wave.

## Why nyxloom owns it

`nyxloomd` is nyxloom's own daemon process and the routes/containment
decisions that determine its memory profile
(`routes.host.toml`, `src/nyxloom/containment.py`,
`nyxloom-trove/nyxloom.toml`) are nyxloom's own config surface. This is a
measurement task against nyxloom's own runtime, not a ciu or infra defect —
ciu's governance mechanism injects whatever `mem_limit` it is given
correctly; the number itself is what is unverified.

## Proposed contract

Run the measurement `nyxloom-daemon.slice`'s own "TO REFINE" note already
prescribes: read `nyxloomd`'s `memory.current` and `memory.stat`'s `anon`
value (a) idle, shortly after daemon start, and (b) during/immediately after
a real multi-task dispatch wave exercising several `trust = "operator"`
routes concurrently (ideally at or near `max_active_tasks = 5` concurrent
uncontained CLIs). Use the measured peak, not the estimate, to replace
nyxloom-P103's interim `6g` in `nyxloomd/ciu.defaults.toml.j2`'s
`[nyxloomd.governance] mem_limit`, with a documented derivation the way
nyxloom-P103's own comment records its (now-superseded) assumption.

## Oracles

- A recorded `memory.current`/`memory.stat anon` reading at both checkpoints
  (idle, under-load), attributed to a specific dispatch wave/workload, exists
  in a nyxloom doc or this entry's own "Updates" section.
- `nyxloomd/ciu.defaults.toml.j2`'s `[nyxloomd.governance] mem_limit` is
  updated to the measured value (or explicitly reaffirmed at `6g` with the
  measurement cited as justification, if that is what the data shows), and
  its comment cites the measurement rather than the analytical estimate.

## SPEC ownership

`infra/slices/nyxloom-agents.slice:13-16`, `infra/slices/nyxloom-daemon.slice:33-37`
(the two "OPERATOR MUST SET"/"TO REFINE" notes this entry answers);
`src/nyxloom/containment.py:204-213`, `routes.host.toml:19-20`
(uncontained-route mechanism); `nyxloom-trove/nyxloom.toml`
(`max_active_tasks`). Originating handoff:
`nyxloom-trove/handoffs/nyxloom-P103-ciu-governance-standalone-roots.md`
Work item 9 and Memory sizing item 2b.

## Updates
