---
kind: backlog-entry
schema_version: 1
id: CP-29
title: "Cheap collector gaps for host monitoring: memory.swap.events, hierarchical memory.events, io control-plane snapshot, io PSI and io.stat in ctl host"
status: open
type: "feature"
component: "metrics"
context_estimate: "small"
provenance: "cgprofile evaluation against the two-game-container host-monitoring need, 2026-10-27 (measured read-only on the host cgroup tree); parent CP-28"
filed_by: "Claude (cgprofile evaluation 2026-10-27)"
spec_owner: "cgroup-profiler sampler"
priority: 2
filed_date: "2026-10-27"
---

## Problem

Measured 2026-10-27 against the host cgroup tree, `lib.metrics.sample_cgroup` already returns io.stat (rbytes, wbytes, rios, wios, dbytes, dios, cost.usage per device), the three PSI files, memory.stat, memory.events.local, swap current/peak, cpu.stat throttling and pids. These signals are missing and each is a small, independent reader addition that CP-28's monitor mode needs but that is useful to existing gate/daemon sessions too:
1. `memory.swap.events` (high, max, fail) is never read (lib/metrics.py reads `memory.swap.current/peak` only). On the measured host the `fail` counter was non-zero on a game-container scope (59434) and its parent slice (72970): swap exhaustion at the cgroup level is invisible today.
2. `memory.events` hierarchical is not read (`memev` is `memory.events.local`, deliberately, to avoid double counting). Add it as a separate `memev_h` group; CP-20 already exposes both for the resource observer, so reuse that code path.
3. Control-plane values never captured: root `io.cost.qos` and `io.cost.model` (per device), presence/value of `io.prio.class`, `io.latency`, block scheduler name and `queue/iosched/prio_aging_expire` for the devices that appear in io.stat. Snapshot at start and on change (fingerprint like `limits.fingerprint`), not per tick.
4. `ctl host` returns memory and cpu PSI per observed slice but no io PSI and no io.stat totals; io is the contended resource in the reference case.
5. io.stat device keys are raw major:minor; add a device-name mapping (from /proc/diskstats or /sys/dev/block) in the manifest.
6. Optional host `/proc/diskstats` per-device counters (reads/writes completed, sectors, ms spent, in-flight, discards, flush) in `sample_host`, for await/util derivation in analyze.

## Acceptance

- Each addition has a fixture-tree test using the existing fixture style (tests/test_metrics.py), including absent-file => `None` (never 0) and counter reset => no negative rate.
- A controlled wrong implementation that reads `memory.swap.events` as zero when the file is absent fails a test.
- Control-plane snapshot appears in the session manifest and changes emit an event; `io.prio.class` absence is recorded as absent, not as an error.
- `ctl host` output gains `pressure.io` and per-slice `io` totals, additive under contract 1 (RG55-INTERFACE-CONTRACT versioning rules).
- Measured per-call cost stays under 1 ms for one cgroup (baseline 0.36 ms).

## Observations recorded with this entry (2026-10-27, read-only)

- No `io.prio.class` file exists at the root, on `system.slice`, `dev.slice`, `dev-background.slice`, or any docker scope although the io controller is enabled (io.stat present): the blk-ioprio policy is not available on this kernel, so per-cgroup IO priority class is not a usable tool here. Data disk scheduler is `[mq-deadline]` with `prio_aging_expire=10000`; iocost is enabled (`io.cost.qos`/`io.cost.model` present at root, `ctrl=user`, linear model).

## Updates

Parent: CP-28. Related: CP-20 (local and hierarchical events), CP-7 (limits table).
