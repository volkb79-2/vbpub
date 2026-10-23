# mdt IO governor (IOGOV) - design

Status: DESIGN, nothing implemented. Written 2026-10-23 from measurements on gstammtisch and the earlier results in
`scripts/gstammtisch-guide/MEASUREMENTS.md` and `HOST-IO-INTERFERENCE.md`. It builds on `libraries/iobench` (measurement authority,
`ceilings.jsonl`, IOB-CEI-01..05, the optional "IOGOV" item in its BACKLOG) and on host-setup's io.cost wiring (`IO_COST_MODEL`,
`IO_COST_QOS`, `mdt-io-cost.service`, `mdt-dev-governance-reconcile`, DESIGN-GUIDE "io.cost is the weight mechanism").

## 1. The problem: the ceiling moves while the disk's identity does not

host-setup treats a baseline as valid until the disk or target identity changes (`mdt-io-baseline.py`: "identity-based, not age-based").
On this host the sustained ceiling has moved by more than 20x in both directions with the same disk identity:

| date | source | sustained write | read | note |
|---|---|---|---|---|
| 2026-07-08 | sustained-v3 baseline | 516 MB/s, 58.7k IOPS | 2149 MB/s, 90k IOPS | game running; burst figures (4.3 GB/s) were hypervisor cache |
| 2026-10-20 | stall A/B, model derived | 14-18 MB/s | 290 MB/s model | `IO_COST_MODEL` wbps 14680064; builds saw a ~6 MB/s floor |
| 2026-10-23 | fio, direct + fsync, 120 s, dev-gates slice | **330-350 MB/s steady** (seq 1M); 243 MB/s without fsync | 992 MB/s seq, 71k IOPS rand 4k | after the storage work; 4k rand write 46.5k IOPS at 0.34 ms |

What moved the ceiling is not the disk's identity but the **provider-side allocation state** (operator account, 2026-10-23): at 2026-10-20 the
management UI reported 1.1 TB used of 1 TB with a snapshot active, which fits the 6-18 MB/s writes (a fixed allocation plus an active snapshot
forces less efficient writes); after the operator freed space, ran `fstrim` and ran the hoster's storage optimisation the guest was at about 50 %
(~500 GB) and sustained 340 MB/s. The guest cannot see a snapshot or the provider's allocation. It can see its own filesystem fill, the discard
history, and the throughput/latency that results.

Effects of a stale figure, both seen in one week:

* Too tight: `DEV_IO_WBPS_MAX=10M` pinned the dev tier at exactly the old floor. The mdt-cockpit build step took **4694 s** under it and
  **~1100-1200 s uncapped** (same image); IO pressure was 30-48 % and fell to 1-9 %.
* Compensated by the controller: with the model 23x too low, `cost.vrate` climbed to 2000-2800 to make up the difference. vrate was
  doing the model's job, which only works while `max` is large enough.
* Too loose (the inverse, already documented in HOST-IO-INTERFERENCE 4.2 A3): a cap above the device's real rate protects nothing.

So "calibrate once per identity" is not enough on a virtual disk whose backing store changes underneath it.

## 2. What the uncapped test builds show (new model, vrate floor 50)

Two builds (mdt-cockpit and its predecessor image family) were sampled every 15 s from `/sys/block/vda/stat` and `io.stat`.

| | mdt-cockpit (78 samples) | mdt-python-debug (77 samples) |
|---|---|---|
| average write / read | 32 / n.m. | 35 / 72 MB/s |
| peak write / read | 130 / 190 MB/s | 143 / 361 MB/s |
| write / read latency | 1-2 ms / 0.2-1 ms | 1-3 ms / 0.3-1 ms |
| device busy (max) | 65 % | 89 % |
| io pressure `some` avg10 (avg / max) | max 9 | 1.1 / 9 (24 in short read bursts) |

Findings that drive the design:

1. Demand is bursty and far below the measured ceiling (average 35 MB/s, peak 143 MB/s against 340 MB/s). A cap near the ceiling never binds
   on a normal build, which is what we want; its only job is to bound a runaway.
2. **vrate sits on the QoS floor.** After the model was corrected (`wbps` 340 MB/s) and `min` was 50, vrate was exactly 50.00 in 58 of 77
   samples (p50 50, max 93) while latencies were 5-25x under the targets (rlat 5 ms, wlat 25 ms). The latency percentile test is not what pulls
   it down. Hypothesis (unverified): the controller's busy detection fires on request-slot waits (`nr_requests`) under write bursts. At vrate 50
   the effective write budget is 170 MB/s, still above the 143 MB/s peak, so nothing was throttled, but there is little margin. The signal that
   matters for the governor: **vrate pinned at either clamp for minutes means the model or the clamp is stale**, whatever the latency says.
3. iobench R6 derives `min = 100`; the value 50 used here was chosen by hand. Which is right is an open A/B (section 7).

## 3. Design: observe, decide, act, with asymmetric trust

A single small service, `mdt-io-governor.service` (Python stdlib, `cli-extended` CLI like the other host tools), one state file under
`/var/lib/mdt/io-governor/`. **It performs no IO of its own and runs no benchmark**: it only reads what the kernel already exposes, keeps the
evidence, and moves settings in small bounded steps. iobench stays the only thing that produces a calibrated model, and only when an operator runs it.

### 3.0 Objective: protect declared workloads, not throughput

The governor is generic: it knows nothing about any application. The operator declares a **protect list** (`IO_GOV_PROTECT`), the workloads whose
IO latency must stay low (here the game server, on another host a database) and whose swapping must not stall them. Everything else
(builds, gates, dev containers) is the **governed** side and gives way first. Throughput of the governed side is secondary.

* **Targets are cgroups, not PIDs.** A PID changes at every container restart; the cgroup path does not. An entry is a cgroup path or glob
  (`/sys/fs/cgroup/system.slice/foo.service`), or `docker:<container name>`, which the governor re-resolves to the container's current scope
  (`docker inspect`-equivalent from `/sys/fs/cgroup` names and the daemon's id) whenever the path disappears or the container restarts.
* **Signals per target, cheapest first** (all plain procfs/sysfs reads, no tracing, no IO of our own):

  | signal | source | cost | tells |
  |---|---|---|---|
  | stall time | the target cgroup's `io.pressure` (`some`/`full` cumulative `total`, deltas per tick) | one small file read | the fraction of time its tasks waited for IO |
  | its IO rates | the cgroup's `io.stat` per device (`rbytes wbytes rios wios`) | one file read | who is doing the IO, and how much |
  | blocked threads | `cgroup.threads` + the state field of `/proc/<tid>/stat`, sampled each tick: threads in `D` | O(threads) reads per tick | IO or reclaim stalls an average hides; the same measure the earlier stall recorder used |
  | device and swap latency | `/sys/block/<disk>/stat`, and the swap partitions read from `/proc/swaps` (auto-detected, no config) | a few reads | the device latency the targets actually see; swap IO is charged to the page owner's cgroup, so a protected workload that swaps depends on it |

* **Optional deep mode: per-cgroup block latency** (`IO_GOV_TRACE=off|on`, default off). The 2026-10-20 investigation used two `bpftrace` programs
  (`scripts/gstammtisch-guide/tools/io-stall/`): `dstall.bt`, which watches `sched_switch` for threads of one TGID blocked in D state >= 20 ms and
  prints total and count per kernel stack, and `wb_delay.bt`, which times `write()` to writeback. Both were pinned to one PID. The governor version would attach a tracepoint
  on `block_rq_issue`/`block_rq_complete` and key the latency histogram by the target's **cgroup id** (`bpf_get_current_cgroup_id()` at issue time),
  so it survives restarts. Cost: the tracepoints fire per request (tens of thousands per second at our peak) and `sched_switch` fires on every context
  switch host-wide with the filter inside the program, so it is real CPU work and needs root, BTF and `bpftrace`/libbpf. Therefore it is off by default,
  time-boxed (`RuntimeMaxSec`), and only started on a hint from the cheap signals (a target's stall time above threshold for N ticks). Fsync latency of a
  target cannot be observed without issuing IO, so it is out of scope.
* The levers are the ones the governed tier already has: its caps (`io.max` on `dev.slice`), the io.cost model and QoS latency targets
  (`rlat`/`wlat`) and weights. The governor tightens the governed side when a protected target's stall time, blocked-thread count or swap latency degrades,
  and relaxes only while all targets are calm.
* **Swap-aware rule:** when swap IO share rises and swap latency or a target's pressure climbs, governed limits are cut harder and faster than for ordinary
  load (swap-in is on the protected workload's critical path; a build's write burst is not).
* Harm to a protected target always wins over any estimate of capacity: the proven-bound logic below only decides how far limits may *relax*.

### 3.0a Covariates the guest can see

* **Filesystem and device fill** (`statvfs` of the root and Docker/BuildKit filesystems, LVM/VG free): the leading indicator of the provider-side
  slowdown. The governor keeps a table `fill bucket -> best proven bound` from its own history, seeded with the two known points
  (>=100 % reported by the provider with a snapshot: 6-18 MB/s; ~50 %: 340 MB/s sustained) and warns at a configurable fill (`IO_GOV_FILL_WARN`) that
  throughput is expected to fall, before it does. The projected bound for the current fill, not the all-time best, is what limits relaxation.
* **Discard history**: when the last `fstrim` ran (`fstrim.timer`/journal). The governor only reads this and reports "free space has not been
  trimmed for N days at fill F"; it never runs `fstrim` (that is IO).
* **Change points**: a step change in the proven bound at a fixed fill (the 2026-10-20 -> 10-23 jump) is logged as a `ceiling_shift` event with
  timestamp and fill. A snapshot or a provider optimisation is invisible to the guest, so `mdt-io-governor note "<text>"` lets the operator annotate
  events ("snapshot created", "storage optimisation done") into `ceilings.jsonl` for later correlation.

### 3.0b Device-only mode: no protect list, just keep the cost model true

The governor must also be useful with **nothing to protect**: no container, PID or cgroup named.
Its job then reduces to the original one, keeping `io.cost` honest, because `vrate` can only do its
work if the model and QoS describe the disk as it is *now*:

- **Too optimistic a model** (the 6 MB/s days): the kernel believes the disk can do more than it
  can, issues too much, device latency rises, `vrate` falls to the floor, and every cgroup is
  throttled hard without a model-side reason.
- **Too pessimistic a model** (what we had before the re-measure): `vrate` is pinned and the
  disk's real headroom is wasted.

Signals that need no workload: per-device latency from `/sys/block/<dev>/stat` (service time per IO),
device utilisation, system-wide `io.pressure` (`/proc/pressure/io`) and the kernel's own `vrate`
(`io.stat` on the cgroup root) and its clamp (`min`/`max`). The inference is the same as 3.1a:

1. If `vrate` sits at the **floor** while device latency is far below the QoS targets
   (`rlat`/`wlat`) and utilisation is low, the model is too pessimistic: raise the model
   (`rbps`/`wbps`/IOPS) and/or lower `min` by a bounded step.
2. If `vrate` is at the floor **and** latency exceeds the targets while IO is substantial, the
   disk really is slow now (or the model is too optimistic): lower the model by a bounded step, so
   the kernel's scale tracks the disk instead of fighting it.
3. If `vrate` varies freely between its clamps, change nothing.
4. With **little IO** nothing can be inferred and nothing is changed; the previous values stay
   (3.1a). No probe, no IO of its own.

Writing a protect list on top only adds a second, workload-level signal and a different goal
(bound the protected workload's p99, not the model's accuracy). The two modes use the same
decide/act/rollback machinery; `IO_GOV_PROTECT` empty selects device-only mode, and that is the
default.

### 3.1 Observe (every 15 s, cheap)

* Device: `/sys/block/<dev>/stat` deltas -> throughput, IOPS, mean latency, busy %, queue depth (the sampler used for this document).
* Controller: `cost.vrate`, per-cgroup `cost.usage` / `cost.wait` / `cost.indelay` from `io.stat`; the clamps from `io.cost.qos`.
* Harm: PSI `io.pressure` (host and every protected target's cgroup), blocked-thread counts and swap-device latency (3.0).
* A **plateau** is >= 60 s with the device busy >= 70 % and throughput within +-15 %. Each plateau is appended to iobench's `ceilings.jsonl`
  with `source: governor` (IOB-CEI-01 requires duration >= 60 s, direction and workload class). This is the population step iobench's
  D-011 says is missing: until now the history is `single-run`.

### 3.1a Inferring the ceiling when little IO happens

Most of the time the disk is idle or lightly used, and an idle window says nothing about capacity. The governor therefore treats every
observation as a **bound, not an estimate**:

* A window with throughput X at healthy latency proves *capacity >= X* (a **lower bound**). Only a saturated window (device busy >= 85 %, or vrate at
  the max clamp, with latency still under target) is close to the ceiling. The ceiling estimate is the best proven lower bound, and it **decays only
  on evidence of failure** (latency over target at a lower throughput), never because the disk was quiet. No saturated window means: hold the
  current values.
* **Bounds are safety rails, not a model of the disk.** The last iobench result cannot be the default range: the ceiling moves by 50x with fill
  level, an active snapshot and the provider's optimisation state, within days. The governor needs only hard rails per direction, set by the
  operator: a **floor** it never sets a limit below (so a build is slowed, not starved: the worst figure ever observed, 6 MB/s, is the natural
  floor) and an **absolute ceiling** (a figure beyond anything the disk has proven, so a cache burst cannot raise limits past it). Between the
  rails the working estimate is a **rolling envelope of what the workload has proven in the last N days, conditioned on fill level** (3.1b), not a
  stored calibration. iobench results, when present, are just one more observation in that history.
* **The controller already measures the ratio.** `cost.vrate` is io.cost's own running estimate of "device capacity / model". vrate at the max
  clamp with latency fine means the model is too low by about `max/100`; vrate at the min clamp *with latency over target* means it is too high.
  The governor re-centres the model by that factor (one bounded step), so the fast loop stays inside its clamps. It does not replace the fast loop.
  vrate at the min clamp with healthy latency (what the builds showed) is **not** treated as "model too high".
* **Random vs sequential.** A sweep on 2026-10-23 (fio, direct, queue depth 16, dev-gates slice) fits one two-term cost per request,
  `t = a + b x size`, which is exactly io.cost's linear model (`*iops` and `*bps` coefficients):

  | size | write MB/s | write IOPS | read MB/s | read IOPS |
  |---|---|---|---|---|
  | 4k | 69 | 17.6k | 138 | 35k |
  | 16k | 109 | 6.9k | 389 | 25k |
  | 64k | 190 | 3.0k | 716 | 11.5k |
  | 256k | 190 | 0.76k | 666 | 2.7k |
  | 1M | 153 | 0.15k | 632 | 0.63k |

  Writes: about 36 us per request plus 5.3 ns per byte; reads: about 22 us plus 1.4 ns per byte. Random 4k is therefore IOPS-limited and large IO is
  bandwidth-limited, and the knee sits near 64k. From the per-window request mix the governor takes the average request size (`sectors / ios`
  from `/sys/block/*/stat`) and classifies the window; it keeps a separate best-proven bound for small-IO (IOPS) and large-IO (bandwidth) windows
  and fits `a`, `b` by least squares over the saturated windows of different sizes. A lower bound seen on one mix is then extrapolated to the other
  only through that fit and only up to the configured ceiling; with fewer than two distinct sizes it extrapolates nothing.
* **Variability is real.** The same 4k random-write test gave 46.5k IOPS earlier on 2026-10-23 (queue depth 16, fresh file) and 17.6k in the sweep
  a short while later, a 2.6x spread within the hour on an otherwise similar host. Steps need two consecutive windows and a cooldown, and the model basis stays the
  worst sustained plateau (D-IOC-2), not the best.

### 3.2 Decide (every 5 min, and on events)

Inputs are the model basis (iobench D-IOC-2: the worst sustained plateau per direction) and the recent observed plateaus.

| observation (sustained, per direction) | meaning | action |
|---|---|---|
| plateau below the model by > 25 % in two consecutive windows | ceiling fell | **lower** model and dev caps toward the plateau (fast path, observation is enough) |
| plateau above the model by > 25 % in two windows (IOB-CEI-04) | ceiling rose or model too low | raise `recalibrate_recommended`; step the model up only if the plateau proves saturation (device busy >= 85 % or vrate at max, latency under target), never above the configured ceiling |
| vrate at the max clamp for > 10 min with latency under target | model too low | as above |
| vrate at the min clamp for > 10 min with latency under target | clamp or request-slot issue | do not touch the model; log, and let the operator or the A/B settle `min` and `nr_requests` |
| latency percentile over target for > 2 min while vrate is at min | device slower than model | lower the model one step |
| a protected target's `io.pressure` stall time above threshold for > 1 min, or its blocked threads persistently > 0 | harm | freeze all increases, step the governed caps down |

Rules shared by every move: a step is at most 25 %; a cooldown of 30 min between steps in the same direction; the model never goes below
0.7 x the worst recorded plateau or above the best proven plateau; `min`/`max` of the QoS follow iobench R6 from the same history; dev caps stay
`DEV_IO_CAP_PCT` of the model, so the caps and the model cannot drift apart again.

Trust is asymmetric on purpose. Lowering is cheap and safe, so one pair of windows is enough. Raising is how a flat provider cap or a cache gets
mistaken for capacity, so it needs a saturated window and is capped by the configured ceiling.

### 3.3 Act

* Writes the model and QoS through the existing `_mdt_apply_iocost` path (the one `install.sh` and `mdt-io-cost.service` use), and the dev caps
  through the reconcile's pinned-value path. No second writer.
* `host-setup.env` keeps operator intent; governor-managed values are marked `auto` and the state file holds the live figure. The reconcile's
  drift check (`_mdt_reassert_io_settings`) must compare against the state file for auto keys, otherwise it undoes the governor within a minute.
* Honours `mdt-io-reassert-pause`.

### 3.4 No probe

The governor never generates load, not even in a quiet window. An increase therefore waits for the workload to prove it (a saturated window, 3.1a).
If the operator wants a fresh figure sooner, they run iobench (`calibrate` / the wizard), which appends a `benchmark` observation the governor then
uses as a bound.

### 3.5 Safety invariants

* Modes `off` | `observe` | `enforce-down` | `enforce` (default `observe`); dry-run logs "would set X to Y because Z".
* Fail-open to the last good values; a governor crash changes nothing.
* Writes only the dev-tier caps, the model and the QoS of the configured root disk. Never `io.max` of a protected target or the system slices.
* Every change is a journal line with before, after, rule and evidence, and an entry in `ceilings.jsonl`.

## 4. Configuration (host-setup.env, wizard step "IO governance")

`IO_GOVERNOR=off|observe|enforce-down|enforce`, `IO_GOV_STEP_PCT=25`, `IO_GOV_COOLDOWN_MIN=30`, `IO_GOV_FLOOR_PCT=70`, `IO_GOV_{R,W}_FLOOR` / `IO_GOV_{R,W}_CEIL` (the range it may never leave),
`IO_GOV_PROTECT=` (the watch list: cgroup paths/globs or `docker:<name>`; its pressure freezes increases), `IO_GOV_TRACE=off|on`, `IO_GOV_FILL_WARN=80` (swap partitions come from `/proc/swaps`). The wizard proposes the first model from an iobench result when one exists.

## 5. Phases

| phase | content | exit criterion |
|---|---|---|
| P0 | `observe` mode: the sampler as a service, journal + `ceilings.jsonl` plateaus, no writes | two weeks of plateaus; the 25 % windows fire on a replay of the 2026-10-20 -> 2026-10-23 shift |
| P1 | `recalibrate_recommended` surfaced in `mdt doctor` and the wizard | operator sees the warning within 24 h of a real shift |
| P2 | `enforce-down` | a simulated ceiling drop lowers caps and model within two windows without harm to a protected target |
| P3 | `enforce` (increases on saturated windows only, bounded by the ceiling) | build time before/after; no step outside floor/ceiling in a replay with injected bursts |

Tests: replay of recorded traces (the logs from this session are the first two), a table-driven decision test for section 3.2, and the VM
harness for the live acceptance. The decision core stays pure (samples in, action out) so it is testable without a host.

## 6. What to configure now (until the governor exists)

Applied 2026-10-23, measured: dev tier 700M read / 250M write bytes/s, 50k / 30k IOPS (about 70 % of the sustained ceiling);
`IO_COST_MODEL="1000000000 70000 70000 340000000 40000 40000"`; `IO_COST_QOS="95 5000 90 25000 50 400"`. Re-measure after any storage change
and whenever a build takes much longer than the ~20 min seen today.

## 7. Open questions

1. Why is vrate at the floor with healthy latency? A/B `nr_requests` and `min` 50 vs 100 (iobench R6) on a replayed build.
2. Does the provider throttle by a flat cap that a plateau detector could mistake for the ceiling? (iobench has "flat" plausibility; reuse it.)
3. Several disks: the design is per root disk; Docker data on another device needs a second governed device.
4. Where the governor runs relative to `mdt-dev-governance-reconcile` (a mode of it, or a second unit sharing the library).
