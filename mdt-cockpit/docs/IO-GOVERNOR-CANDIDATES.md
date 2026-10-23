# IO governor: candidate designs and how to test them against each other

Companion to [IO-GOVERNOR-DESIGN.md](IO-GOVERNOR-DESIGN.md) (requirements, history, safety rules).
That document fixes the goal; this one lists the competing ways to reach it and a test plan that picks
between them with measurements instead of arguments.

**Goal in one sentence.** Notice when the storage's real capability changes a lot (340 MB/s → 15 MB/s →
back) and re-fit the `io.cost` model (and the caps derived from it) so that the kernel's own controller can
keep the protected workload's latency low. No IO of its own, no benchmark probes, works without knowing
what the workload is.

## 1. The candidates

Every candidate runs as a small loop that reads counters, decides, and writes the model/QoS through the
one existing apply path. They differ only in **what they read and when they act**.

### A. `vrate` edge ("act when the kernel runs out of range")

Read the kernel's own speed factor `vrate` (percent of the model it currently allows; clamped by the QoS
`min`/`max`). If it sits at (or within a few percent of) the floor for N periods, lower the model by a step;
if it sits at the ceiling, raise it. The model then follows the disk until `vrate` is mid-range again.

- **Pros:** the simplest thing there is; no extra sensors; universal; closed loop on the kernel's own opinion.
- **Cons:** `vrate` falls at the floor and then *stays* there while load is light (the kernel only raises it
  when it sees demand it is holding back). On this host it sat at 50 in 58 of 77 samples with healthy
  latency. A bare rule would lower the model forever (spiral to the rail) although the disk is fine. The
  floor can also mean "request slots are exhausted", which a lower model does not fix.
- **Variant A+:** act only when the floor coincides with evidence of device trouble (missed latency
  targets, rising queue depth) and only raise when demand was actually held back.
- **Complexity:** very low (A), low (A+). **Overhead:** one file read per period. **Risk:** spiral (A),
  slow recovery.

### B. Quiet-window service time

Read `/sys/block/<dev>/stat`; keep only windows with about one request in flight; track the time one
request takes, per direction and size band; compare the last 30 minutes with the last 7 days; act on a
3× change that holds.

- **Pros:** needs no saturation; cheap; unaffected by our own throttling (throttling delays requests before
  they reach the disk, it does not lengthen service time).
- **Cons:** a provider cap on bandwidth or IOPS may not change single-request time at all, so it can miss
  exactly the failure we had; needs enough quiet windows; first estimate needs days of history.
- **Complexity:** medium. **Overhead:** one file read, small ring buffers.

### C. Saturated-window ceiling

Keep only windows in which the *disk* was the limit: latency rising with queue depth, delivered rate not
close to a cap we set, demand present. In such a window the delivered bandwidth/IOPS is a lower bound of the
capability. Track a high percentile of these over a sliding window with decay, and fit the model to it.

- **Pros:** measures the thing that matters (what the disk delivers when pushed) and sees a bandwidth or IOPS
  cap directly; works with any workload mix.
- **Cons:** needs load to learn; on a quiet host the estimate is stale (it must say so and hold); the
  "disk is the limit" test must be right or we read our own limits as the ceiling; recovery is only seen
  when someone pushes again.
- **Complexity:** medium-high. **Overhead:** one file read plus the queue-depth arithmetic.

### D. Hybrid (B as early warning, C as the number)

A drop in quiet-window service time, or a saturated window delivering far less than the model, raises a
suspicion; the model changes only when C confirms (or when B alone is overwhelming and persistent). `vrate`
is shown, not used.

- **Pros:** covers both failure types (latency up, throughput cap down); two independent signals reduce
  false alarms.
- **Cons:** most code, most knobs, hardest to explain.
- **Complexity:** high. **Overhead:** as B plus C.

### E. Static baseline (control)

The current setup: values from `host-setup.env`, re-measured by hand.

### F. Wide kernel range, no governor (control)

The iobench rule "price the worst plateau": model priced at the worst observed capability, QoS `max` wide
so `vrate` climbs by itself.

### G. Oracle (test only)

Knows the true capability set by the test harness; sets the model exactly and instantly. Not a real option:
it is the upper bound against which A–D are scored.

## 2. Comparison (to be filled with measurements)

| | A | A+ | B | C | D | E | F |
|---|---|---|---|---|---|---|---|
| Needs history | no | no | days | hours | days | no | no |
| Sees bandwidth/IOPS cap | via vrate only | via vrate | no | yes | yes | no | no |
| Sees latency-only slowdown | via vrate only | via vrate | yes | partly | yes | no | no |
| Spiral risk | high | low | low | medium | low | none | none |
| Works on an idle host | no signal | no signal | yes | holds | holds | n/a | n/a |
| Complexity | very low | low | medium | med-high | high | none | none |
| Tunables | 3 | 5 | 6 | 7 | 10+ | 0 | 0 |

## 3. Test plan: a VM whose disk we control

The real host cannot be used for this: its disk is shared with production and we cannot make it slow on
command. A VM can have a disk whose speed we change from outside, while the governor inside changes
`io.cost`, exactly as on the real host.

**Harness.** The existing QEMU harness (`scripts/debian-install-v2/testing/vm`, TCG, virtio disk). Additions:
- a **data disk** (`virtio-blk`, so `io.cost` attaches as on the host) with QEMU I/O throttling set from
  outside through QMP (`block_set_io_throttle`: bandwidth and IOPS, changeable while running). This is the
  simulated "provider": drop it, raise it, flap it on a schedule. Latency-only slowdowns: a throttle with a
  low IOPS cap, or `dm-delay` inside the guest;
- the same **cgroup tree** as the host (a protected tier, a dev tier, a build slice, swap on a separate
  device) and the same `io.cost` model/QoS/`io.max` renderers (`host-setup` scripts), so the governor
  is tested against the real apply path.

**TCG means slow.** Absolute speeds are far lower than the host's (single-digit MB/s is realistic); that is
fine because the question is *relative*: capability falls by 20× and returns. All time constants are
expressed in sampling windows, so a scenario of minutes stands for the real hours. Scenarios are scaled
(for example 60 → 3 MB/s instead of 340 → 15).

**Storage for the VM.**
- OS image: a qcow2 base on the **btrfs lab subvolume** (`/srv/devcontainers-cow/vm-lab`, zstd), one cheap
  reflink clone per run. Verified: the subvolume can be bind-mounted into the running devcontainer at
  `mdt-cockpit/artifacts/vm-lab` without recreating it (a helper clones the mount with `open_tree` and
  moves it into the container's mount namespace; it must be repeated after a container restart).
- Test data disk: a **sparse raw file on tmpfs** (RAM), sized small (1 to 2 GB). Reason: the throttle sets
  the speed, so the backing store should be neutral and must not add IO to the production disk; tmpfs
  costs only the pages actually written. The host has about 4 GB available, so the footprint is capped.
- Everything under `artifacts/` stays out of git.

**Scenarios** (each scored per candidate; same seed):
1. step down and recover (60 → 3 → 60 MB/s equivalent);
2. gradual decline over many windows;
3. flapping (down 2 windows, up 2 windows), to expose oscillation;
4. latency-only slowdown, bandwidth unchanged;
5. bandwidth cap with unchanged per-request time;
6. an idle period of hours-equivalent, then load;
7. a burst of cache-fast small writes on a slow disk (the "provider cache" case);
8. long steady state (false-alarm rate).

**Load profiles** (fio plus a memory hog): idle with a game-like service (small synchronous writes with
fsync, periodic saves), build-like bursts (large sequential writes with fsync), mixed, and a swap storm
(memory pressure with swap on the throttled device).

**What is measured** (the protected workload is the point):
- p50/p99 write latency and stall time of the protected service, per scenario and candidate;
- time to detect a drop and to recover after a rise;
- dev-tier throughput kept (not stalling builds forever is also a goal);
- number of `io.cost` writes and the range of the model (stability, no spiral, no flapping);
- final model error against the true capability;
- governor CPU, memory and IO.

A candidate that never lowers the protected p99 below the static baseline (E) does not pass, however
elegant. The oracle (G) bounds what any candidate can reach.

## 4. Order of work

1. Review these candidates (fresh review, not by the author), fold in findings.
2. Add the throttle control and the cgroup/`io.cost` renderers to the VM harness; verify a throttle change
   is visible to `/sys/block/vda/stat` and to `io.cost` inside the guest.
3. Implement candidates as pure decision functions over a stream of samples (replayable from the real
   15 s samples of 23 Oct and from VM runs), plus a thin runner.
4. Run the scenario matrix, fill the table in section 2, choose, then build the service (P0 observe-only).
