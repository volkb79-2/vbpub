# gstammtisch — Environment Observation & Interpretation

> How to read the host's state: what to look at, the command, and **what the
> output means**. Values shown are from `gstammtisch` where we captured them.
> Companion to [MEMORY-ARCHITECTURE.md](MEMORY-ARCHITECTURE.md).
> Quick all-in-one snapshot: [`scripts/swap-health.sh`](scripts/swap-health.sh) (`swap-health` / `swap-health watch`).

---

## 1. zswap configuration — `/sys/module/zswap/parameters/`

```bash
grep -r . /sys/module/zswap/parameters/ 2>/dev/null
```
On this kernel (7.0.10) the writable knobs are:

| Parameter | Want | Meaning |
|---|---|---|
| `enabled` | `Y` | zswap on |
| `compressor` | `zstd` | active compressor. **If it reads `lzo`, the zstd post-boot fix didn't run** — see [MEMORY-ARCHITECTURE.md §3](MEMORY-ARCHITECTURE.md). |
| `max_pool_percent` | `30` | *ceiling* on pool size (% of RAM). Not a reservation — grows only to hold pages that would otherwise hit disk. |
| `accept_threshold_percent` | `90` | only matters once the pool has already hit its ceiling: resume accepting new pages once usage falls back to this % *of the ceiling*. It does not do anything before the ceiling is reached, and it is not a general fill target. |
| `shrinker_enabled` | **it depends — see below** | proactively writes compressed pages to disk before the pool fills. Not a "Want Y" default; read the caveat before flipping it on a live host. |

> All five are runtime-writable, so the whole config is driven post-boot by
> `zswap-config.service` (no GRUB tokens). The early `dmesg` line
> `zswap: compressor zstd not available, using default lzo` is **expected and
> cosmetic** — what matters is the value above after boot.

**Disk overflow itself is intentional, by design — this is not a bug to
"fix" toward zero disk swap.** The tiering this whole guide builds is RAM →
zswap (compressed, fast) → real disk (last resort). `memory.zswap.writeback=1`
on the game slices deliberately lets the genuinely-cold tail spill past zswap
once the pool has nothing colder to evict (see `instance-defaults.env`'s own
`SOULMASK_WRITEBACK` comment and M3/M4 below) — the design goal is *some*
pages on disk, not none. What actually matters is whether the pages that
land there stay cold once they're there.

**`shrinker_enabled` has no "keep the pool at ~80%" middle ground — it is
all-or-nothing per cgroup, confirmed live 2026-09-08.** There is no kernel
knob that says "evict cold pages down to a target fill level, then stop."
The three knobs above are the entire menu: `max_pool_percent` is a ceiling,
`accept_threshold_percent` only fires after the ceiling is already hit, and
`shrinker_enabled` is a generic, pressure-driven, cross-memcg LRU shrinker
with no per-cgroup floor and no target percentage — it keeps writing back
whatever it judges coldest, for as long as memory pressure exists, with
nothing to stop it at a partial point. Live incident evidence, twice in one session (2026-09-08): enabling it on a
genuinely memory-constrained two-instance host first fully drained one
instance's zswap pool from ~650-680M to **0** over about seven minutes while
the other, busier instance's pool was untouched in the same window — then,
re-enabled later to see if the untouched instance would eventually go the
same way, it drained that instance's much larger ~2.7G pool to **0 in about
2.5 minutes**, this time with a directly-observed severe stall (in-game FPS
crashed to 8.3, `rfd/s` peaked past 45,000/s, `disk_sw` jumped ~5.5G in the
same window). Two for two: it is not one unlucky memcg, it reliably
evacuates whichever pool it currently judges coldest, all the way to zero,
with real player-facing impact while doing it. Turning it back off (`N`)
stops the drain immediately; it does not un-drain what's already on disk. If you
need this smoothing behavior without the all-or-nothing risk, the only real
option is a small userspace watcher toggling `shrinker_enabled` around a
target band — not shipped anywhere yet, filed as a debian-install-v2 feature
request (search its backlog for "zswap governor").

Confirm GRUB actually passed any cmdline you set, and what the kernel booted with:
```bash
cat /proc/cmdline
dmesg | grep -i zswap
```

### What the post-drain state incidentally revealed — the actual design target

Historically, a player login that touches a large previously-cold region
(their account/inventory/nearby-world state) has been one of the worst
trigger points on this host: it's exactly the "big, sudden anon-memory
touch" shape that produces a refault storm, and the player-visible result
has usually been bad enough that the first login attempt drops and a second
try is needed.

**2026-09-08, same session as the two shrinker-drain incidents above:**
MAIN's zswap pool had been sitting at a flat **0M** for roughly 20 minutes
(the aftermath of the second drain, `shrinker_enabled` left off since) when
a player logged into MAIN while CLIENT already had players connected. The
login produced a real, measurable refault burst — `rfd/s` (anon refault
from disk) peaked at 1135/s around the same few seconds MAIN's `RAM`/`anon`
climbed by roughly 100-150M (4873M→4982M) — but this time the game's own
FPS only dipped into the high-teens/low-20s (from a ~22-24 baseline) and
recovered within seconds. No `—` (RCON-unresponsive) marker appears
anywhere in the monitor output for MAIN across this entire ~19-minute
window. No disconnect, no second attempt needed.

**Why this isn't "empty pool = good, full pool = bad" — it's about waste,
not just timing.** The zswap pool, in its normal working-as-designed steady
state (pegged near its ceiling — see §2's `pool_limit_hit` counter), is
mostly holding pages that operator experience with this game says will
**never be touched again**: dead entity/building/inventory state for
players and areas that are no longer active. Compression (~3x observed
ratio) shrinks that dead weight, but does not zero it — a genuinely-dead
90% of the pool is still occupying real RAM for zero future benefit,
permanently, as long as it sits there. Writing it to real disk instead
costs nothing further once it's there (it is, by definition, never read
back), while whatever small fraction *is* still warm simply refaults back
in on demand — the only way to actually learn which pages were warm is to
let them prove it by being touched again. Net effect: pushing everything to
disk and letting only the genuinely-warm subset refault back naturally ends
up holding **less** total RAM resident than continuously keeping a
compressed pile of mostly-dead pages parked in zswap. That is a standing
RAM-occupancy cost, independent of whether a login ever creates contention
with it at all — the contention framing in an earlier draft of this section
undersold it: even with no login ever happening, a pool full of guaranteed-
dead pages is wasted RAM the whole time it sits there.

**This does not mean "drain zswap to zero" is the fix — the two incidents
directly above show the drain *itself* causes exactly the kind of severe
stall this section is worried about (MAIN's FPS crashed to 8.3 during the
act of draining, not after).** The actual design target this observation
points at is: keep the pool comfortably *below* its ceiling at all times,
continuously, by gradually evicting only genuinely old/cold pages to disk
in the background — so there is always headroom available before a login
spike ever arrives — while protecting recently-touched ("warm") pages from
ever being swept up in that eviction, so the pages a player is actually
about to touch again aren't the ones being pushed to disk. That is exactly
the combination (target fill percentage + minimum page age + paced,
non-bursty eviction) already filed as the zswap-governor feature request
and researched in the feasibility report:
[`scripts/debian-install-v2/TODO.md`](../debian-install-v2/TODO.md) /
[`zswap-shrinker-threshold-feasibility.md`](../debian-install-v2/zswap-shrinker-threshold-feasibility.md).
This live login is the concrete "here's what good looks like" evidence for
why that fix is worth building, not just "here's what bad looks like."

**Interim mitigations, until the real fix exists:**
- **Manual full-drain reset** (`shrinker_enabled=Y` briefly, then back to
  `N`, let the pool re-populate from scratch with only what's genuinely
  touched going forward) — a coarse, manual version of the target-fill
  governor. Only do this during a deliberately-chosen low-traffic window,
  never with players actively online: the drain itself is the damaging
  part (both incidents above), not a side effect of the *result* being
  empty.
- **Lower `accept_threshold_percent`** (currently 95) as a cheap, safe,
  independent lever worth testing — but understand what it actually does
  before expecting much from it: it only governs *resuming* acceptance
  after the pool has already hit `max_pool_percent` once, requiring it to
  shrink further (via ordinary refault, no shrinker involved) before
  refilling. It slows *future* accumulation of dead weight per cycle; it
  does nothing to the dead pages already resident right now. Complementary
  to the manual reset above, not a substitute for it.

---

## 2. zswap runtime stats — `/sys/kernel/debug/zswap/`

```bash
sudo grep -r . /sys/kernel/debug/zswap/ 2>/dev/null
```
Counters present on this kernel and how to read them:

| File | Meaning / what to watch |
|---|---|
| `stored_pages` | pages currently held compressed in the pool. `>0` = zswap is doing work. |
| `pool_total_size` | bytes the pool occupies. Compare to `stored_pages×4096` for the **effective compression ratio** (want ~3×). |
| `written_back_pages` | pages the shrinker evicted to **disk**. Rising fast vs `stored_pages` ⇒ pool pressure (raise `max_pool_percent`, or add RAM). |
| `pool_limit_hit` | times the pool hit its ceiling. Frequent ⇒ pool too small for the working set. |
| `reject_compress_poor` | pages that didn't compress well enough to be worth storing. |
| `reject_compress_fail` / `reject_alloc_fail` / `reject_kmemcache_fail` / `reject_reclaim_fail` | store rejections by cause — usually low; spikes indicate memory/allocator stress. |
| `stored_incompressible_pages` | pages stored even though they don't compress (already-compressed/encrypted data). |
| `decompress_fail` | should be `0`. Anything else is a real problem. |

Rule of thumb: **healthy** = high `stored_pages`, ~3× effective ratio, `written_back_pages` a small fraction of `stored_pages`, `decompress_fail=0`.

---

## 3. The right swap metrics (not `vmstat si`)

`vmstat`'s `si` mixes fast zswap-pool decompressions with slow disk reads into one misleading number. Watch instead:

```bash
grep -E 'pgmajfault|pswpin|pswpout|zswpin|zswpout|zswpwb' /proc/vmstat
```
| Counter | Meaning |
|---|---|
| `pgmajfault` | **major faults = real disk reads**. The trend that matters; spiking = thrash. |
| `zswpin` / `zswpout` | pages faulted **in/out of the zswap pool** (fast, in-RAM). |
| `zswpwb` | pages **written back to disk** by zswap (the slow tier). |
| `pswpin` / `pswpout` | raw swap device in/out. |

Memory pressure (PSI) — the single best "is the box struggling" signal:
```bash
cat /proc/pressure/memory          # 'some'/'full' avg10/avg60/avg300
cat /sys/fs/cgroup/dev-workloads.slice/memory.pressure   # per-slice
```
`full avg10`: `0` good · `<5` warning · `>10` critical (real stalls).

---

## 4. Swap devices

```bash
swapon --show        # NAME TYPE SIZE USED PRIO — per-device, incl. priority
cat /proc/swaps
free -h
```
Equal `PRIO` across `gswap1`/`gswap2` ⇒ kernel round-robins (interleaves) across them. Per-partition I/O — the reason we used partitions over a swap file:
```bash
iostat -dx 2 vda6 vda7
```
(A swap *file* on the LV would fold its I/O into `dm-0`/`vda5` and be invisible here.)

---

## 5. Disk class, TRIM & I/O scheduler

```bash
cat /sys/block/vda/queue/rotational
lsblk -do NAME,ROTA,DISC-GRAN,DISC-MAX /dev/vda
cat /sys/block/vda/queue/scheduler
```
gstammtisch returned (after BFQ switch):
```
rotational = 1
NAME ROTA DISC-GRAN DISC-MAX
vda     1      512B       2G
scheduler = none mq-deadline [bfq]
```
Interpretation:
- **`DISC-MAX=2G` (and `DISC-GRAN=512B`) ⇒ TRIM/discard is supported ⇒ thin-provisioned backend** (network/SAN/qcow2/LVM-thin). This is the signal that matters → use `discard=once` on swap (TRIM the area at activation; reclaims backing cheaply). Avoid *continuous* `discard` (per-free latency).
- **`rotational=1` is almost certainly the hypervisor's default, not truth** — don't tune for a spinning disk. Treat as SSD/thin: keep `vm.page-cluster=0`. (Confirmed by `r_await ≈ 0.3 ms` observed with `iostat -x` — typical of SSD-backed thin storage, not the 5–15 ms of a real HDD.)
- **`scheduler [bfq]`** — we switched from the VM default `[none]` to BFQ. Rationale below.

### Why we use BFQ instead of `[none]`

The conventional advice for VMs is "leave `[none]` — the hypervisor handles scheduling." That is correct for raw throughput. It is **wrong** when you need cgroup I/O priorities: `[none]` passes all I/O to the device queue in arrival order; cgroup `io.weight` and `ionice` classes are entirely ignored.

BFQ (Budget Fair Queueing, `CONFIG_BFQ_GROUP_IOSCHED=y`) is the only multi-queue scheduler that enforces cgroup v2 `io.weight` / `io.bfq.weight`. It also exposes the per-cgroup `io.bfq.weight` knob (range 1–1000). On this host, Soulmask holds `io.bfq.weight=1000` and bench containers hold `io.bfq.weight=1` — a 1000:1 ratio that is only meaningful with BFQ active.

The thin-provisioned backing storage (`r_await ≈ 0.3 ms`) means BFQ's scheduler overhead is negligible; the benefit of weight enforcement far outweighs the marginal latency.

```bash
# Confirm BFQ is active
cat /sys/block/vda/queue/scheduler   # → none mq-deadline [bfq]

# BFQ is loaded as a module — verify
lsmod | grep bfq   # → bfq  NNN  0

# Check Soulmask's BFQ weight (only present when BFQ is the active scheduler)
SOUL_PID=$(docker top b87c0a5b-2387-4a1c-8863-ff23e6800a1d 2>/dev/null | awk '/WSServer/{print $2}' | head -1)
SOUL_CG=/sys/fs/cgroup$(awk -F: '/^0::/{print $3}' /proc/$SOUL_PID/cgroup)
cat $SOUL_CG/io.bfq.weight   # → default 1000
```

The `bfq` module loads at boot via `/etc/modules-load.d/bfq.conf`; the udev rule `/etc/udev/rules.d/60-bfq-scheduler.rules` switches `vda` to BFQ on device enumeration, before `setup-cgroups.sh` runs. See [SOULMASK.md §2b](SOULMASK.md) for full I/O isolation details.

Partition layout & free space (MBR, root on logical `vda5`, free space *inside* the extended partition):
```bash
fdisk -l /dev/vda
sfdisk -d /dev/vda          # machine-readable dump (also what partition-editor.py parses)
sfdisk -F /dev/vda          # free regions
scripts/partition-editor.py --disk /dev/vda free   # free regions, both primary & logical
scripts/partition-editor.py --disk /dev/vda list
```

---

## 6. cgroup v2 — per-slice memory, zswap, IO

```bash
# which cgroup a process is in
cat /proc/$(pidof WSServer-Linux-Shipping 2>/dev/null || echo self)/cgroup

# Soulmask container scope (find via its process)
for c in $(docker ps -q); do docker top "$c" 2>/dev/null | grep -q WSServer && echo "$c"; done

# per-slice usage / protection / pressure
cat /sys/fs/cgroup/dev-workloads.slice/memory.current
cat /sys/fs/cgroup/dev-workloads.slice/memory.zswap.current   # how much zswap this slice uses
cat /sys/fs/cgroup/dev-workloads.slice/{memory.high,memory.max,memory.zswap.writeback}
cat /sys/fs/cgroup/dev-workloads.slice/io.stat
# on the Soulmask scope: memory.min, memory.low, memory.zswap.writeback (want 0)
```

---

## 7. KSM (if enabled)

```bash
ls /sys/kernel/mm/ksm/
grep -H . /sys/kernel/mm/ksm/{run,pages_sharing,pages_shared,general_profit,full_scans}
```
- `run=1` = scanning marked regions. `pages_sharing` = pages saved by dedup; `pages_shared` = unique pages backing them.
- **`general_profit`** = estimated bytes saved minus overhead. Low/negative ⇒ KSM isn't paying off for this mix; turn it off.

---

## 8. DAMON — measure Soulmask's hot set before setting `memory.min`

The whole protection scheme depends on `memory.min` ≈ the *real* hot+warm working set. Kernel 7.0 has **interval auto-tuning**, so you no longer hand-tune sampling intervals. Use your existing DAMON tooling to classify the Soulmask PID's regions during normal gameplay, take `hot + warm` bytes, add a buffer, and set that as `SOULMASK_MIN` in [`setup-cgroups.sh`](files/usr/local/sbin/setup-cgroups.sh). Err high — game "cold" pages go warm in bursts (world saves, joins, AI ticks); under-protecting causes fault-back stutter.

---

## 9. One-glance health

```bash
swap-health          # snapshot (zswap stats, ratio, writeback %, swap devices, PSI, faults)
swap-health watch    # refresh every 5s
```
Green board: `compressor=zstd`, healthy compression ratio, `written_back_pages` a small fraction of `stored_pages`, `pgmajfault` flat, PSI `full avg10` near 0, no OOM kills in `dmesg | grep -i 'killed process'`.
