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
| `accept_threshold_percent` | `90` | once the pool hits its limit, stop accepting new pages until it shrinks to this % — damps fill/evict thrash. |
| `shrinker_enabled` | `Y` | proactively writes coldest compressed pages to disk before the pool is exhausted. **OFF by default** — must be set. |

> All five are runtime-writable, so the whole config is driven post-boot by
> `zswap-config.service` (no GRUB tokens). The early `dmesg` line
> `zswap: compressor zstd not available, using default lzo` is **expected and
> cosmetic** — what matters is the value above after boot.

Confirm GRUB actually passed any cmdline you set, and what the kernel booted with:
```bash
cat /proc/cmdline
dmesg | grep -i zswap
```

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
