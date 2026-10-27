---
kind: backlog-entry
schema_version: 1
id: CP-28
title: "Monitor mode: standing sampler over named cgroup scopes with IO/PSI/swap collectors and stall-episode analysis (host monitoring reference case)"
status: open
type: "feature"
component: "sampler"
context_estimate: "large"
provenance: "Operator request 2026-10-27: monitoring two production game-server containers on a shared host; iomon vbpub@d1a19a159 (branch iomon-20261027), IO governor v1 recorder vbpub@2db1712aa"
filed_by: "Claude (cgprofile evaluation 2026-10-27)"
spec_owner: "cgroup-profiler sampler"
priority: 2
filed_date: "2026-10-27"
---

## Problem

Operators of a shared host need continuous, fine-grained (1-5 s) cgroup-level evidence for "who is stalling whom" on storage and memory. The reference case: two production game-server containers run on a shared host (host cgroup v2 tree, one shared data disk); dev/agent workloads (`dev.slice` and children, build and gate containers) share the same disk and swap. The operator needs per-cgroup IO, PSI, memory and swap signals for the game containers' docker scopes, the dev slices, `system.slice` and the root ("who else writes"), written as JSONL over hours to days, with stall episodes detectable afterwards.

cgprofile already has most of the per-cgroup readers (measured 2026-10-27, see Evidence) but no way to point them at a standing set of named scopes without a gate lane around them. Two ad-hoc tools now exist that each re-implement part of it:
- `iomon` (branch `iomon-20261027`, vbpub@d1a19a159 / vbpub@5aba27feb; `mdt-cockpit/scripts/iomon.py`, `iomon_bpf.py`): 5 s stdlib sampler of /proc/diskstats, PSI, vmstat, docker stats of two named containers, a 4 KiB O_DSYNC probe, `analyze` with stall-episode detection and cross-container correlation, plus a bpftrace container.
- IO governor v1 recorder (worktree `agent-a12c7e7bacd247601`, vbpub@2db1712aa; `mdt-cockpit/iogov/{collectors,episodes,covariates}.py`): reads cgroup `io.stat`/`io.pressure` of governed/protected cgroups, resolves `docker:<name>` to its `docker-<id>.scope` via `config.v2.json`, PSI-based distress episodes.

Both parse `io.stat`/PSI/`memory.stat` and resolve container scopes themselves. That is cgroup measurement and belongs in cgprofile.

## Evidence (what cgprofile does today)

Measured read-only 2026-10-27 on the shared host, from one short-lived root observation container (host `/sys/fs/cgroup` bind-mounted read-only, repo mounted read-only, `lib.metrics.sample_cgroup` / `lib.limits.read_limits` called directly):
- `sample_cgroup` (lib/metrics.py, groups `mem memstat memev psi cpu io pids cgstat`) returns, for a game-container scope, a slice, `system.slice` and the root: full `io.stat` per device including `rbytes wbytes rios wios dbytes dios cost.usage`, `io/memory/cpu.pressure` (avg10/60/300 + totals), `memory.stat` (anon, file, file_dirty, file_writeback, pgmajfault, workingset_refault_*), `memory.events.local`, swap current/peak, `cpu.stat` (`nr_throttled`, `throttled_usec`), `pids.current/peak`. Cost: 0.36 ms per call for all groups, one cgroup.
- `read_limits` (lib/limits.py:124) reads `io.max` (per device), `io.weight`, `memory.high/max/swap.max`, `pids.max`; `effective()` walks the chain and attributes the binding level.
- Gaps found: `memory.swap.events` (the `fail` counter was non-zero on both a game scope and its parent slice) and `memory.events` (hierarchical) are not sampled (memev reads `memory.events.local` only); `io.cost.qos` / `io.cost.model` / `io.prio.class` are never read; no /proc/diskstats (per-device latency); `ctl host` (lib/serve.py) returns memory and cpu PSI per slice but no io PSI and no io.stat; the daemon's `ctl start` accepts only `--target containerid:<64 hex>` with `--meta` (RG55 contract section 2.2, a run-gate lane identity) and `--scope container|container-shared`, so a standing monitor of named scopes and slices cannot use the daemon; `attach` (one-shot, helper container) takes `slice:`/`cgroup:`/`container:NAME` targets and `--duration`/`--until-file` but defaults to adaptive 0.25-2.0 s intervals and ends in report generation; the host daemon sampler interval is fixed per session (default 1.0 s).
- Access: from the devcontainer `cgprofile doctor` reports `host_cgroup_view False`, `--mode direct` is refused, `--mode helper` (privileged helper container, `--cgroupns=private`, host `/sys/fs/cgroup` read-only, host `/proc` at `/hostproc`) works for `attach`/`run`. The long-lived host daemon (`cgprofile-host-daemon`, socket `/run/cgprofile/ctl.sock`) answers `ctl version` and `ctl host` from the devcontainer.

## Proposal

A first-class **monitor mode** for cgprofile: a long-lived, gate-independent sampler over a declared set of named cgroups, writing the existing JSONL sample schema, with a streaming/analysis story for stall episodes. Sketch (all open to design):
1. `cgprofile monitor` (or `serve --monitor <spec>`): targets `slice:NAME`, `cgroup:/path`, `container:NAME|ID` (exact docker name or 64-hex id, re-resolved when the container is recreated), root, with a fixed interval (default 5 s, selectable 1-5 s), no phase marks, no report at the end, size/age-bounded rotation of `samples.jsonl.gz`.
2. Extra collectors, each off by default and opt-in per target: `memory.swap.events`, hierarchical `memory.events`, `io.cost.qos`/`io.cost.model`/`io.prio.class`/`io.latency` presence and value (control-plane snapshot at start and on change), host `/proc/diskstats` per-device deltas with derived latency (r/w await, util, queue), host vmstat (already in `sample_host`).
3. PSI-based stall-episode detection over the samples (shared with `events.py`), emitting episode records with pre/post context into `events.jsonl`.
4. `cgprofile analyze` over a monitor run: per-cgroup IO and PSI timelines, episode table, cross-cgroup correlation (who wrote during each stall).
5. `ctl host` gains io PSI and io.stat totals per observed slice.

## Acceptance criteria (the signal list)

Per observed cgroup (the two game-server docker scopes, `dev.slice` and its children, `system.slice`, root), per interval of 1-5 s, as raw counters plus deltas that never go negative across cgroup recreation:
- `io.stat`: rbytes, wbytes, rios, wios, dbytes, dios, cost.* per device (cost.usage today; any other cost.* key the kernel exposes), with major:minor mapped to a device name.
- `io.pressure`, `memory.pressure`, `cpu.pressure`: some/full avg10/60/300 and totals.
- `memory.stat`: anon, file, file_dirty, file_writeback, pgmajfault, workingset_refault_anon/file, swapcached.
- `memory.events` (local AND hierarchical): high, max, oom, oom_kill; `memory.swap.current`, `memory.swap.events` (high, max, fail).
- `cpu.stat`: nr_throttled, throttled_usec, usage_usec; `pids.current/peak`.
- Limits and control plane, snapshotted at start and on change: `io.max`, `io.weight`, `memory.high/max/swap.max`, `pids.max`; root `io.cost.qos` and `io.cost.model`; presence (and value when present) of `io.prio.class`; block scheduler name and `prio_aging_expire` for the data disk.
- Per-device latency from /proc/diskstats deltas (await, util) alongside the cgroup series.
- Container targets resolved by exact docker name or exact 64-hex id, and re-resolved after a container restart; never by prefix or label for a monitor target; never starts, stops or inspects any other container.
- Runs unattended for 24 h with bounded disk use (rotation) and a measured overhead recorded in the docs: target at most 1 percent of one core and 64 MiB RSS for 2 game scopes plus 10 slice/scope targets at 1 s (baseline measured: 0.36 ms per scope per tick for all groups).
- Runs in a root container with the host cgroup tree mounted read-only (no `--privileged`, no host PID/cgroup namespace, `--cap-drop=ALL`, `--network=none`, placed in a declared observer slice), and the same output when run natively on the host.
- A JSONL schema document (versioned) shared with at least one other consumer (see IO governor below); a controlled wrong implementation that drops `swap.events` or reports a negative delta after a cgroup recreation fails a test.

## Open design questions

1. Daemon or one-shot: extend `serve` with standing monitor sessions (its session registry, retention, socket, resource observer exist) versus a separate `monitor` subcommand that reuses `sampler.py`/`metrics.py`/`store.py` without the RG55 contract. The daemon's `ctl start` is hard-wired to gate lanes (`containerid:` + `--meta` + `RUN_GATE_PROFILE_SESSION`), so reuse needs a consumer-neutral session kind (same direction as CP-15's neutral name).
2. Sample rate model: fixed 1-5 s, or keep adaptive hot/idle (0.25-2.0 s) and add a fixed mode; adaptive is wrong for a regular time series feeding stall detection, fixed is wrong for a gate.
3. Privileges and mounts: the minimal read-only invocation (see Evidence); who may start it (docker group socket vs operator-installed systemd unit, as P8 did for `dev-gates.slice`); how a monitor avoids contaminating what it observes (its own placement slice, no fsync probes).
4. Schema sharing with iogov and iomon: adopt one record schema (per-cgroup `io`, `psi_*`, `memstat`, `memev`, `swapev`; per-device `diskstats`) so iogov reads cgprofile output instead of re-parsing, and so iomon's `analyze` becomes a cgprofile analyze view.
5. Retention: count/age defaults (CP-1) are tuned for gate sessions; a standing monitor needs byte-bounded rotation and an explicit "keep until" for incident windows.
6. Episode detection: one definition shared by cgprofile `events.py` and iogov `episodes.py` (thresholds in config, pre/post ring), or cgprofile emits samples only and consumers detect.
7. Time base: sample timestamps (wall + monotonic) good enough to align with a bpftrace stream and an O_DSYNC probe from another tool.

## Relationship to the IO governor and iomon (separation of concerns)

cgprofile = cgroup measurement (what each cgroup did and suffered, continuously, as data). iogov = disk-capability probing plus recommendations (raw 4 KiB reads, adaptive scratch write bench, CUSUM recognizer, covariates such as fstrim and mount options, recommend-only). Decision per component:
- Moves into cgprofile (this entry): cgroup `io.stat`/`io.pressure`/`memory.*` collectors, docker-scope resolution (`resolve_containers` in iogov `collectors.py`; iomon's per-container docker-stats reader is superseded by cgroup files, which are cheaper and unaffected by docker API stalls), named-scope standing sampler, per-device diskstats deltas (device-level, but only as a covariate series), PSI stall-episode detection, JSONL schema, analyze/report for episodes and cross-cgroup correlation.
- Stays in iogov: the active O_DSYNC / raw read / scratch-write probes (they issue IO and are a measurement of the disk, not of a cgroup), CUSUM recognizer, covariates (fstrim, discards, mount options, provider signals), budget/gating, recommendations, and the `io_prio_available` capability check (it may call cgprofile's control-plane snapshot).
- Stays separate (its own tool): the bpftrace/eBPF collector (`iomon_bpf.py`, `iomon-bpf/`): kernel tracing with its own privileges and container; cgprofile may ingest its output as an extra stream later, but should not embed it.
- Consumer relationship: iogov consumes cgprofile JSONL (episodes, per-cgroup series) as an input and attributes; cgprofile never recommends tuning.

## Folds / depends

Related: CP-1 (retention), CP-2 (mounted socket), CP-8 (liveness/watch streaming), CP-15 (consumer-neutral daemon session naming), CP-16 (summary series). Needs a design decision before carving: daemon-extension versus separate subcommand (question 1).

## Next step

Operator picks the daemon-vs-subcommand direction; then a design note in `DESIGN.md` (new section) and a carve into: (a) collectors + schema, (b) monitor runner + rotation, (c) analyze episodes, (d) iogov/iomon migration.
