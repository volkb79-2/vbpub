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

Effects of a stale figure, both seen in one week:

* Too tight: `DEV_IO_WBPS_MAX=10M` pinned the dev tier at exactly the old floor. The mdt-cockpit build step took **4694 s** under it and
  **~1100-1200 s uncapped** (same image); IO pressure was 30-48 % and fell to 1-9 %.
* Compensated by the controller: with the model 23x too low, `cost.vrate` climbed to 2000-2800 to make up the difference. vrate was
  doing the model's job, which only works while `max` is large enough.
* Too loose (the inverse, already documented in HOST-IO-INTERFERENCE 4.2 A3): a cap above the device's real rate protects nothing.

So "calibrate once per identity" is not enough on a virtual disk whose backing store changes underneath it.

## 2. What the uncapped test builds show (new model, vrate floor 50)

Two builds (mdt-cockpit, modern-debian-tools-python-debug) were sampled every 15 s from `/sys/block/vda/stat` and `io.stat`.

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

### 3.1 Observe (every 15 s, cheap)

* Device: `/sys/block/<dev>/stat` deltas -> throughput, IOPS, mean latency, busy %, queue depth (the sampler used for this document).
* Controller: `cost.vrate`, per-cgroup `cost.usage` / `cost.wait` / `cost.indelay` from `io.stat`; the clamps from `io.cost.qos`.
* Harm: PSI `io.pressure` (host and the game's cgroup), D-state counters where the stall recorder exists.
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
* **An upper bound comes from configuration, not from the disk.** The governor needs a range it may never leave: per direction a floor and a
  ceiling (`IO_GOV_{R,W}_FLOOR`, `IO_GOV_{R,W}_CEIL`, default from the last iobench result, else from the operator). The ceiling is the best sustained
  figure ever proven (or a provider plan limit); nothing observed passively can justify a value above it, and raising it is an operator or
  iobench act. This is what stops a cache burst or a flat provider cap from being mistaken for capacity.
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
| game `io.pressure full avg10` >= 2 for > 1 min | harm | freeze all increases, step dev caps down |

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
* Writes only the dev-tier caps, the model and the QoS of the configured root disk. Never `io.max` of the game or system slices.
* Every change is a journal line with before, after, rule and evidence, and an entry in `ceilings.jsonl`.

## 4. Configuration (host-setup.env, wizard step "IO governance")

`IO_GOVERNOR=off|observe|enforce-down|enforce`, `IO_GOV_STEP_PCT=25`, `IO_GOV_COOLDOWN_MIN=30`, `IO_GOV_FLOOR_PCT=70`, `IO_GOV_{R,W}_FLOOR` / `IO_GOV_{R,W}_CEIL` (the range it may never leave),
`IO_GOV_GAME_CGROUP=` (the cgroup whose pressure freezes increases). The wizard proposes the first model from an iobench result when one exists.

## 5. Phases

| phase | content | exit criterion |
|---|---|---|
| P0 | `observe` mode: the sampler as a service, journal + `ceilings.jsonl` plateaus, no writes | two weeks of plateaus; the 25 % windows fire on a replay of the 2026-10-20 -> 2026-10-23 shift |
| P1 | `recalibrate_recommended` surfaced in `mdt doctor` and the wizard | operator sees the warning within 24 h of a real shift |
| P2 | `enforce-down` | a simulated ceiling drop lowers caps and model within two windows without game harm |
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
