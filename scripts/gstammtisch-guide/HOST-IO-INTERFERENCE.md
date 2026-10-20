# HOST-IO-INTERFERENCE.md — why host IO stalls a compute-only game server, and what bounds it

*2026-10-20. Measured on gstammtisch (16 GiB RAM, kernel 7.2.6, root ext4 on LVM over virtio `vda`,
BFQ scheduler) unless a line says otherwise. Source investigation:
`IO-STALL-TESTING-GUIDE.md` (controller working guide, release-program directory) and the instrument
scripts under [`tools/io-stall/`](tools/io-stall/README.md).*

This is the **WHY** document for the host IO findings (the README says WHAT the guide covers; the
how-to for operators is the "Applying" section here plus [SOULMASK-TMPFS.md](SOULMASK-TMPFS.md) and
[the egg doc](../../game_stuff/soulmask/README.md)).

**Evidence labels.** Every claim carries one: **[M date host]** measured, **[I]** inferred (reasoned
from measurements, not itself tested). Nothing in the "Recommended settings" section is applied yet.

## 1. The mechanism

- **The game is compute-only.** [M 2026-10-20 gstammtisch] Its main threads make `futex`, `poll`,
  `clock_nanosleep` and `epoll` syscalls. Its only ext4 writes are `WS/Saved/Logs/WS.log` (thread
  `FAsyncWriter_WS`, with `fdatasync`) and, with players online, the SQLite files under `WS/Saved` (`world.db`,
  `account.db`).
- **The stall comes from the shared root ext4 journal, not from the game's own IO.** [M 2026-10-20
  gstammtisch] Root is ext4 in `data=ordered` mode. In that mode a journal commit first writes the
  dirty data attached to the transaction, including other workloads' data. The game blocks in
  `jbd2_log_wait_commit`, `folio_wait_writeback` and `ext4_dirty_inode` → `wait_transaction_locked`,
  behind dev writeback it never issued.
- **The stall length is a queueing wait:** (bytes ahead of it) ÷ (device write rate). [M 2026-10-20
  gstammtisch, A/B below] The ahead-of-it bytes are the ordered dirty data in the running
  transaction plus the requests in the block queue.
- **The device is slow.** [M 2026-10-20 gstammtisch] It writes ~14–18 MB/s, and ~10–11 MB/s once a
  burst budget was used up (~04:40 UTC in the matrix; consistent with a provider token bucket **[I]**).
  The vda 1 s counters never exceeded this under any setting.
- **Other host (do not mix numbers).** r1002 (IO-BENCH-01, `IO-BENCH-01-report.md`) is a different
  VM: GB/s-class, no token bucket seen. Nothing in this document transfers to it without
  re-measurement.
- **Side effect.** [M 2026-10-20 gstammtisch] Heavy dev IO also froze VS Code and ssh on the host. The
  same shared queue and journal are the cause **[I]**.

## 2. Why the obvious metrics mislead

| Trap | Why it misleads |
|---|---|
| The game's `io.stat` says "no IO" | True, and irrelevant: it blocks on *other* workloads' queue. Look at where its threads block, not at how much IO it issues. [M 2026-10-20] |
| iostat `%util` | On a parallel virtio disk 100 % only means "at least one request was always in flight". Read `aqu-sz` and `r_await`/`w_await`, on `dm-0` (the LVM layer, where buffered writeback queues up) **and** `vda`. |
| Per-cgroup `io.max` / `io.bfq.weight` | They limit throughput or order requests per cgroup. They do not shorten a wait behind the shared journal. Throttling the other side can even lengthen the wait (priority inversion: the throttled task holds the transaction open). [M 2026-10-20 for `io.max`: A3 vs A2; the inversion itself is **[I]**] |
| The July "BFQ shielded the game" result | A 4-minute fio run in `system.slice` with no journal sharing showed game `io.pressure full avg300` 0.05 % (MEASUREMENTS M6). That load did not contend on the game's fsync path. The 2026-10-20 runs, which did, gave game `io.pressure some` 23–48 % (A0–A3) while the *host* `io.pressure full` reached 69–76 %. Different load shape and different PSI kinds (`some` vs `full`, game vs host), not a contradiction. |
| Game-cgroup memory PSI | Stayed ~0 during the stalls (`memory.min` protects the working set). The stall is not memory. [M 2026-10-20] |

## 3. Instruments and how to read a D-state stack

All read-only. They ran from `/var/log/mdt-stall/` on the host as transient systemd units with
`RuntimeMaxSec`. Generic copies belong in `tools/io-stall/` (see its README).

| Instrument | Answers |
|---|---|
| `soulmask-monitor.sh --json 2` (RCON `fps` every 2 s) | does the game itself slow down? The ground truth for "the player feels it" |
| `stall_recorder.py <pid> rec.jsonl` (1 s) | per second: `vda` counters and `inflight`, host and game-cgroup PSI, the game's `memory.stat`, `/proc/vmstat` (direct reclaim, dirty, writeback), dev/system/wings `io.stat`, the game's net counters, every game thread in D state with `wchan` and kernel stack |
| `bpftrace dstall.bt <tgid>` | per kernel stack: total ms and count of game-thread blocks of 20 ms or more, per 10 s window. Needs BTF (`/sys/kernel/btf/vmlinux`), **no kernel headers, no reboot** |
| a `raw_syscalls` bpftrace one-liner | which syscalls the game makes and which are slow (> 10 ms) |
| `stall_summary.py <seconds> [end_epoch]` | one screen per window: queue and latency, who did the IO, PSI, fault and reclaim rates, the fps distribution |

Packages (Debian 13): `sysstat` (iostat), `bpftrace`, `strace` (stops threads at every syscall: not
on the live game), `fio`, `python3`. `bpfcc-tools` is optional and needs kernel headers.

**Reading a D-state stack:**

| Top frames | Meaning |
|---|---|
| `do_swap_page` → `folio_wait_bit_common` | page fault on a swapped-out page waits for the disk read |
| `filemap_fault` | fault on an evicted file page (e.g. executable code) waits for the disk read |
| `ext4_sync_file` → `file_write_and_wait_range` → `folio_wait_writeback` | `fsync`/`fdatasync` waits for its own pages to reach the disk |
| `jbd2_log_wait_commit` | `fsync` waits for the journal commit, which carries other workloads' ordered data |
| `ext4_dirty_inode` → `jbd2__journal_start` → `wait_transaction_locked` | even a plain `write()` (an mtime update) waits for the running transaction to close |
| `balance_dirty_pages` | a writer is throttled because too much dirty memory exists (healthy for the dev side) |
| `__alloc_pages_slowpath` / `try_to_free_pages` | direct reclaim |

## 4. Results

### 4.1 Baseline and the realistic load (L1)

[M 2026-10-20 gstammtisch] Baseline fps 29.2–29.9; the only D-state waits an occasional `fdatasync`
(max ~0.2 s). With no dev limits and an image build plus the canary gate in parallel: dm-0 `aqu-sz`
~744 at `w_await` ~859 ms, `FAsyncWriter_WS` blocked up to **7.3 s**, fps fell to 26.8 for ~25 s.

### 4.2 A/B: kernel settings under a fixed synthetic load

[M 2026-10-20 03:15–03:57 UTC gstammtisch.] 240 s per phase, then cooldown. Load in `dev-gates.slice`:
a dd loop writing new 1 GiB files from `/dev/urandom` (buffered) plus fio 4k randwrite, 2 jobs,
`fsync=16`. Probe (`system.slice`): append 200 bytes plus `fdatasync` every 0.5 s on the same
filesystem. All settings restored afterwards and verified.

| phase | settings | probe fdatasync p50 / p99 / max | game D max | 10 s windows D ≥ 1 s | fps min | vda wMB/s | w_await | aqu-sz |
|---|---|---|---|---|---|---|---|---|
| BASELINE | quiet host | 1 ms / 34 ms / 2.0 s | 1.6 s | 1 | 29.9 | 0.9 | 5.5 ms | 0.2 |
| A0 | no dev limits, defaults (`dirty_ratio` 20/10) | 0.23 s / 34.7 s / 35.1 s | **26.4 s** | 10 | **7.4** | 18.3 | 697 ms | 66.5 |
| A1 | `dirty_bytes` 256M / bg 64M | 0.68 s / 23.7 s / 23.7 s | 21.6 s | 18 | 16.8 | 13.7 | 2852 ms | 62.3 |
| A2 | A1 + `nr_requests` 32 | 0.87 s / 8.9 s / 10.0 s | 6.2 s | 25 | 20.6 | 14.1 | 740 ms | 13.7 |
| A3 | A2 + dev caps (dev 80M/40M, gates 60M/30M) | 0.65 s / 8.3 s / 10.6 s | 6.6 s | 23 | 26.1 | 13.3 | 486 ms | 12.7 |
| **A4** | `dirty_bytes` 64M / 16M, `nr_requests` 16, `max_sectors_kb` 256, no caps | **0.15 s / 0.54 s / 0.86 s** | **0.4 s** | **0** | **29.7** | 17.6 | 58 ms | 6.6 |

Reading it:

1. **Bounding the bytes in flight removes the stall without losing throughput** (A4 17.6 MB/s against
   A0's unbounded 18.3: the same device throughput, within run noise). Dev writers are throttled in `balance_dirty_pages` (their own process)
   instead of in the shared journal. Game `io.pressure some` fell from 23–48 % (A0–A3) to 1.7 %.
2. **The dev caps changed almost nothing** (A3 vs A2): the device saturates below the cap, so `io.max`
   never binds.
3. **`write()` latency is the price.** Plain `write()` p99 was 0.1 ms in A1 but 80–400 ms in A2–A4
   (the `ext4_dirty_inode` → `wait_transaction_locked` path). Far below the multi-second stalls.

### 4.3 Matrix: dirty limit × queue depth × request size

[M 2026-10-20 04:32–04:50 UTC gstammtisch.] 13 cells of 60 s; before each cell dirty and writeback
drained below 4 MiB and 20 s quiet; interleaved order; REF (the A4 setting) first and last; no caps.
`d` = `dirty_bytes` MiB (background = d/4), `q` = `nr_requests`, `r` = `max_sectors_kb`.

| cell | in flight | probe fdatasync p50 / p99 / max | game D max | fps min | vda wMB/s | fio iops | fio fsync p99 |
|---|---|---|---|---|---|---|---|
| REF1 d64 q16 r256 | 4 MB | 64 ms / 716 ms / 1.08 s | 0.96 s | 29.9 | 16.7 | 196 | 0.97 s |
| d16 q4 r1024 | 4 MB | 194 ms / 1.08 s / 1.27 s | 1.21 s | 29.8 | 16.7 | 68 | 2.06 s |
| **d16 q16 r256** | 4 MB | **59 ms / 330 ms / 498 ms** | **0.27 s** | 29.9 | 16.7 | **275** | **0.66 s** |
| d16 q8 r512 | 4 MB | 59 ms / 485 ms / 620 ms | 0.31 s | 29.9 | 16.6 | 223 | 0.91 s |
| d64 q8 r1024 | 8 MB | 109 ms / 759 ms / 836 ms | 0.51 s | 29.6 | 16.6 | 150 | 1.67 s |
| d16 q4 r256 | 1 MB | 81 ms / 480 ms / 480 ms | 0.29 s | 29.9 | 11.9 | 172 | 0.87 s |
| d16 q16 r1024 | 16 MB | 179 ms / **12.3 s / 12.3 s** | **9.3 s** | **21.6** | 10.9 | 76 | 4.73 s |
| d16 q8 r256 | 2 MB | 58 ms / 345 ms / 562 ms | 0.45 s | 24.7 | 10.8 | 209 | 0.57 s |
| d64 q8 r512 | 4 MB | 122 ms / 756 ms / 829 ms | 0.58 s | 29.8 | 11.0 | 136 | 1.64 s |
| d16 q4 r512 | 2 MB | 202 ms / 640 ms / 1.39 s | 0.58 s | 29.8 | 9.6 | 76 | 1.47 s |
| d16 q8 r1024 | 8 MB | 158 ms / 1.03 s / 1.70 s | 1.52 s | 29.6 | 10.8 | 75 | 2.20 s |
| d16 q16 r512 | 8 MB | 133 ms / 1.10 s / 1.27 s | 0.78 s | 29.7 | 10.9 | 140 | 1.45 s |
| REF2 d64 q16 r256 | 4 MB | 49 ms / 399 ms / 445 ms | 0.40 s | 29.9 | 11.0 | 354 | 0.72 s |

Findings:

1. **Noise floor ~2×.** REF1 and REF2 (identical settings) differ 2× in p99. In 60 s cells a smaller
   difference is not significant.
2. **Large requests are the worst knob.** All four `r1024` cells are in the bottom half; 68–150 fio
   iops against 172–354 for `r256`; the only bad cell is d16 q16 r1024 (12.3 s fsync, 9.3 s game stall,
   fps 21.6). 512 KB and 1 MB requests are not better than 256 KB. Likely mechanism **[I]**: at
   10–17 MB/s one 1 MB request occupies the device for 60–100 ms and a small journal write queues
   behind whole requests.
3. **256 KB requests with queue depth 8–16 are best on both sides** (fsync p99 0.33–0.40 s, game D max
   ≤ 0.45 s, highest fio iops). Queue depth 4 did not help further.
4. **16 vs 64 MiB dirty is not decisive within the noise** (d16 q16 r256 330 ms vs REF 399/716 ms).
   16 MiB bounds the worst case tighter: (16 + 4) MB ÷ 10 MB/s ≈ 2 s against ~7 s for 64 MiB **[I]**.
5. **The device write rate fell ~35 % mid-run under every setting** (16.7 → 10–11 MB/s from ~04:40,
   no other writer; matches a drained burst budget **[I]**). The worst cell sits at that transition,
   so part of its 12 s may be throttling onset; its ranking stays the same either way.

### 4.4 Matrix with 64/128/256 KB requests

[M 2026-10-20 05:51–06:23 UTC gstammtisch.] `ab_matrix2.sh`: 8 settings × 2 repetitions (a, b), 90 s
cells, interleaved (the a-order, then the b-order reversed). Same load, probe, drain and quiet gap as
section 4.3; no dev caps. The device was in its throttled state (~10 MB/s) for almost every cell.
Restore verified (`dirty_ratio` 20/10, `nr_requests` 256, `max_sectors_kb` 4096); the dev.slice cap had to
be re-applied afterwards (see the systemd hazard in section 6). Each cell shows a / b. `d` = `dirty_bytes`
MiB, `q` = `nr_requests`, `r` = `max_sectors_kb`.

| setting | probe fdatasync p50 | p99 | max | game D max | fio iops | fio fsync p99 |
|---|---|---|---|---|---|---|
| **d16 q16 r64** | 3.7 / 1.9 ms | 104 / 203 ms | 165 / 354 ms | 0.09 / 0.11 s | 210 / 203 | 1.65 / 1.74 s |
| **d16 q8 r64** | 16 / 2.0 ms | 129 / 127 ms | 255 / 133 ms | 0.41 / 0.09 s | 182 / 206 | 1.89 / 1.65 s |
| d16 q8 r128 | 3.7 / 21 ms | 105 / 221 ms | 124 / 461 ms | 0.19 / 0.30 s | 236 / 209 | 2.11 / 2.40 s |
| d16 q16 r128 | 33 / 35 ms | 280 / 466 ms | 352 / 559 ms | 0.18 / 0.51 s | 234 / 189 | 2.40 / 2.57 s |
| d16 q16 r256 | 26 / 62 ms | 342 / 523 ms | 401 ms / 1.20 s | 0.20 / 0.52 s | 243 / 155 | 1.47 / 2.87 s |
| d16 q8 r256 | 69 / 70 ms | 898 / 545 ms | 1.95 / 1.05 s | 1.62 / 0.84 s | 123 / 145 | 3.61 / 3.41 s |
| d32 q16 r128 | 61 / 85 ms | 199 / 570 ms | 202 / 643 ms | 0.10 / 0.23 s | **79 / 110** | **7.0 / 5.2 s** |
| d32 q16 r256 | 200 / 132 ms | 1.49 s / 553 ms | 2.08 s / 728 ms | 1.86 / 0.33 s | **52 / 91** | **10.0 / 4.7 s** |

All cells kept fps ≥ 28.9.

Findings:

1. **Smaller requests lower the latency of the small sync, monotonically: 64 KB < 128 KB < 256 KB.** With
   64 KB requests the probe's median fdatasync is 2–4 ms (25–70 ms at 256 KB), p99 100–200 ms, the worst
   case ≤ 354 ms and the game D-stall ≤ 0.41 s. It holds in both repetitions, so it is above the 2× noise
   floor.
2. **Smaller requests do not cost the gate-like load anything here.** fio iops 182–210 at 64 KB against
   123–243 at 256 KB (dirty 16 MiB, q8 and q16). Device write throughput is bound by the device (~10 MB/s throttled)
   in every cell.
3. **A 32 MiB dirty limit is clearly worse for the gate-like load:** 52–110 iops with a 5–10 s fsync p99,
   against ~200 iops and 1.5–2.6 s at 16 MiB. At 256 KB it is also worse for the probe. Keep
   16 MiB / 4 MiB.
4. **Queue depth 8 vs 16 at 64 KB is within the noise.**
5. **Not measured: read-throughput impact.** `max_sectors_kb` also caps read requests (the sequential
   reads of builds and image layers). Before persisting 64 KB, measure sequential read MB/s at 64 KB
   against 4096 KB.

**Recommendation (not applied):** `vm.dirty_bytes=16777216`, `vm.dirty_background_bytes=4194304`,
`nr_requests=16` (8 is also fine), `max_sectors_kb=64`, **subject to the read-throughput check**
(finding 5). 128 KB is the fallback if reads suffer.

### 4.5 Caveats (not proven)

- **A4 changes three knobs at once;** the A/B does not separate `dirty_bytes`, `nr_requests` and
  `max_sectors_kb` (the matrix partly does).
- **Short, synthetic, 60–90 s cells.** Not yet confirmed with the realistic L1 load over a longer run
  that crosses the burst-to-steady transition. One confirmation (top 3 cells × 2 repetitions × 90 s,
  interleaved, inside the throttled steady state, ~12 min) is still to run.
- **Everything ran under BFQ.** `none` and `mq-deadline` are untested. BFQ keeps its own internal
  queueing, so how `nr_requests` interacts with it is **[I]** from the numbers, not from the code.
- **Read throughput at small `max_sectors_kb` is unmeasured** (section 4.4 finding 5).
- **Nothing is persisted.** All values were applied at runtime and restored.
- **`vm.dirty_bytes` is host-global** and also bounds every production container's writeback.

## 5. Recommended settings (NOT APPLIED; a host-setup work item)

| Knob | Recommended | Reason |
|---|---|---|
| `vm.dirty_bytes` / `vm.dirty_background_bytes` | 16 MiB / 4 MiB (measured). 32/8 MiB was also measured (section 4.4, 90 s, throttled state) and is clearly worse for the gate-like load: keep 16/4 | bounds the ordered-data backlog a journal commit can wait for: backlog ÷ rate. 256M/64M (A1) was still 21 s |
| `/sys/block/<root disk>/queue/nr_requests` | 8–16 | bounds the device queue; the matrix (section 4.3, same dirty limit) shows q4 gives no further gain over q8–16. **[I]** the A/B's 6 s at 32 (A2) also carried `dirty_bytes` 256M, so it does not isolate `nr_requests` |
| `/sys/block/<root disk>/queue/max_sectors_kb` | 64 (fallback 128), subject to a read-throughput check | 1 MB requests were the worst cell; 512 KB was no better than 256; 64 KB beat 128 and 256 KB for the small sync in both repetitions (section 4.4). It also caps read requests, which is unmeasured |
| scheduler | keep `bfq` (measured under it) | `none`/`mq-deadline` untested. **[I]** bfq is what makes the dev tiers' cgroup weights order *queued requests* at all; it does not help the journal wait (section 6) |
| dev `io.max` | derive from the ~10 MB/s steady state; do not pin literals | at the 40M cap the device saturated first, so the cap never bound (A3). Caps are the second line, after the queue bounds |

Rules for whoever implements it:

- Set via `sysctl.d` and a udev rule. Select the disk by the device that backs `/` (resolve it),
  never by a name such as `vda`.
- Derive the values from a device-rate measurement and a target wait bound (e.g. (dirty + in-flight)
  ÷ steady rate ≤ 2 s), not as literals. A faster host (r1002) needs different numbers.
- Each value gets a documented reason per knob (see the estate version-policy rule for floors).
- The existing July advice in this guide predates this and was never derived from the steady write
  rate: the 200 MB/s `io.max` (see the banner in [GAMINGHOST-SWAP-2.md](GAMINGHOST-SWAP-2.md)), the
  40 % `BE_IO_CAP_PCT` dynamic caps of the 516 MB/s baseline (table row and note in
  [plan-host-resource-governance.md](plan-host-resource-governance.md) §3.1, [MEASUREMENTS.md M6](MEASUREMENTS.md)),
  and the 31M/100/400 boot fallback (`IOWriteBandwidthMax=/dev/vda 31M` and its siblings in the unit
  listing of `plan-host-resource-governance.md`, also quoted in `MEMORY-ARCHITECTURE.md` §5).

## 6. Cgroup IO controllers: what fits and what does not

- **`io.max` (dev tiers).** Useful as a throughput ceiling only if derived from the steady state.
  Does not shorten the journal wait. [M 2026-10-20: A3 vs A2]
- **systemd spelling hazard for any script that touches slice IO limits.** [M 2026-10-20 gstammtisch,
  systemd 257] systemd keeps one in-memory IO limit entry *per device path spelling*. An empty
  `IOReadBandwidthMax=` resets an entry to `max` but keeps it, and `systemctl show` hides all-max entries.
  After the matrix scripts cleared and re-set limits via `/dev/vda` while the MDT reconcile sets them via
  `/dev/mapper/…-root`, a stale all-max entry won at apply time and `dev.slice`'s kernel `io.max` was
  **empty (uncapped), silently**. `systemctl daemon-reload` rebuilt the list from the drop-ins and the
  reconcile then verified again. Rule: use the same spelling as the MDT reconcile, or run
  `systemctl daemon-reload` afterwards. The MDT-HS-04 fix adds a one-shot self-heal.
- **`io.bfq.weight`.** Orders requests between cgroups at the scheduler. Does not help once the
  game's `fdatasync` waits for a journal commit carrying other cgroups' data. [I]
- **`io.latency`: does not fit.** Not compiled in on Debian 13 (`CONFIG_BLK_CGROUP_IOLATENCY` not set;
  checked 2026-07 in `/boot/config`). Even if it were, it throttles other cgroups after the game's
  *block-layer* latency rises; the game's wait is in the *journal*, upstream of the block layer. [I]
- **`io.cost` (`io.cost.qos`): a candidate, untested for this problem.** Compiled in
  (`CONFIG_BLK_CGROUP_IOCOST=y`). A device-wide latency QoS could shrink the queue the same way
  `nr_requests` does, but it works at issue time and, like the other controllers, does not see the
  journal ordering. Worth an A/B only after the plain queue bounds are persisted. Note the io.cost
  model is fed by `mdt-io-baseline.py`; its July sustained-v3 figures (wbw 516 MB/s, MEASUREMENTS M6)
  are ~30× the 14–18 MB/s measured on 2026-10-20, so re-run the baseline before trusting any model
  derived from it. [I]

## 7. Structural options and their costs

| Option | Effect | Cost |
|---|---|---|
| `WS.log` on a per-instance tmpfs (SMT-01, **merged**) | the log's fsync leaves ext4 entirely | logs lost on restart/reboot; size and sibling logs are operator responsibility, see [SOULMASK-TMPFS.md](SOULMASK-TMPFS.md#operator-responsibilities-and-known-gaps) |
| `eatmydata` in the game's `LD_PRELOAD` | removes the game's own sync waits (mainly SQLite) | not crash-consistent across host crash/power loss; plain `write()` still waits on the journal. See [the egg doc](../../game_stuff/soulmask/README.md#eatmydata-optional-fsync-removal) |
| separate filesystem for game data (own journal) | dev writeback no longer shares the game's journal | migration of the Wings volumes, an extra device or LV; the shared device queue stays, so the queue bounds are still needed |
| root `data=writeback` | commits stop carrying the ordered data | weaker crash semantics for all root files (stale blocks after a crash); cannot be switched by remount on a mounted root, so needs fstab plus a reboot |
| loop devices | (not an option) | **never on prod**: a `--commit` loop-device swap that was detached without `swapoff` caused a real host hang and reboot on 2026-09-09, which also hit the production game server |

## 8. What to monitor

| Signal | Where | Healthy / alarm |
|---|---|---|
| `aqu-sz`, `w_await` on `dm-0` and `vda` | `iostat -x 1` | dm-0 aqu-sz in the hundreds = backlog behind the journal |
| in-flight requests | `/sys/block/vda/inflight` | should stay near the `nr_requests` bound once set |
| host PSI io `full`, game-cgroup PSI io | `/proc/pressure/io`, the game's `io.pressure` | game `some` below a few % (A4: 1.7 %; A0: 24 %) |
| dirty / writeback pages | `/proc/vmstat` (`nr_dirty`, `nr_writeback`) | bounded near `dirty_bytes` once set |
| game D-state stacks | `bpftrace dstall.bt` | any `jbd2_log_wait_commit` / `wait_transaction_locked` over 1 s |
| fps and network tx | RCON `fps`, `/proc/<pid>/net/dev` | fps below ~25 or tx flat for seconds = the player-visible stall |

## 9. Applying (when the work item is approved)

Runtime, reversible, **CHANGES STATE**, outside a game session:

```bash
# ROOTDEV = the whole disk under / (here vda). Resolve it, never type it:
#   lsblk -s -no NAME,TYPE "$(findmnt -no SOURCE /)"     # take the row whose TYPE is "disk"
ROOTDEV=<that disk name>
sysctl vm.dirty_bytes=16777216 vm.dirty_background_bytes=4194304
echo 16  > /sys/block/$ROOTDEV/queue/nr_requests
echo 64  > /sys/block/$ROOTDEV/queue/max_sectors_kb   # trial value; 128 is the fallback if sequential reads suffer
```

Persist through `sysctl.d` and a udev rule only after the confirmation run in section 4.5. Revert
values (defaults on this host): `dirty_bytes` 0 (ratio mode 20/10), `nr_requests` 256,
`max_sectors_kb` 4096.

## 10. What this doc superseded (removal ledger, SMT-02, 2026-10-20)

Each wrong or stale IO claim found in the guide, where it was, and what happened to it. No operator
command or still-valid procedure was removed; every removed claim has a one-line pointer here.

| File, section | Claim | Action |
|---|---|---|
| `SOULMASK.md` §2b "The problem that existed" | the IO issue is "device utilization from random seeks" (3.4 M vs 674 K IO counts) | removed; "Superseded 2026-10-20" note |
| `SOULMASK.md` §2b "Live settings" | BFQ 1000:1 means the bench gets the disk only when the game's queue is idle | rewritten: ordering of queued requests only, no help for the journal wait |
| `SOULMASK.md` §2b "Live settings" | `io.max` caps keep the device queue clean for DB saves; headroom keeps the device off saturation | rewritten with the A3 vs A2 evidence (caps never bind, device saturates at ~15 MB/s) |
| `SOULMASK.md` §2b "Why `io.latency` is absent" | `io.cost.qos` gives the game 99.98 % of tokens and is a "second enforcement layer" | condensed to "candidate, untested"; the example command is kept, marked not applied |
| `SOULMASK.md` "Soulmask save & backup mechanism" | save is in-memory, "no disk I/O" | kept, hedged "per the egg text"; measured note added that the SQLite files are synced on the shared journal with players online |
| `SOULMASK.md` §2c | the chest-open stall is the file-cache stall | kept; one clause separates it from the host-IO stall |
| `SOULMASK.md` §1 | egg named `egg-soulmask.json` | corrected to the real egg file |
| `MEASUREMENTS.md` M6 | BFQ/io.weight alone "nearly fully shielded" the game (PSI 0.05 %) | kept as history; "do not generalise" correction; 516 MB/s write figure flagged as ~30× the measured rate; cap-validation recipe kept with a qualifier |
| `plan-host-resource-governance.md` §3.1 table row `io.max`, the rationale paragraph, the §7 step-5 text, the unit listing | 40 % dynamic caps, 31M/100/400 fallback, "IO latency protected by BFQ weight plus io.max" | kept as design history, each qualified with a correction note |
| `MEMORY-ARCHITECTURE.md` §3 and §5 | disk swap-in is "the dominant cause of game stalls"; 31M/100/400 fallback | scoped to memory-pressure stalls; note on the caps |
| `OBSERVATION.md` BFQ section | BFQ 1000:1 protects the game; `r_await` 0.3 ms means a fast disk | qualified; the weak write rate added |
| `CGROUP-MONITORING.md` `io.bfq.weight` | BFQ prioritises the game over all other cgroups | qualified |
| `WINGS-CGROUPS-ROLLOUT.md` finding 4 | `io.cost` is the real fix | kept (still valid measurement); pointer added, `io.cost` marked candidate |
| `wings-cgroups/v1-legacy/README.md` | `io.cost` "would address the mechanism more directly"; eatmydata "considered and rejected" | both qualified with pointers; the rejection's reasoning is kept as the risk statement, the 2026-10-20 opt-in is recorded |
| `GAMINGHOST-SWAP-1.md`, `GAMINGHOST-SWAP-2.md` | `io.max` 200 MB/s and 100M/50M examples | "Superseded IO advice" banner; the swap analysis is untouched |
| `SOULMASK-TMPFS.md` "Shortcomings of the legacy split-ramdisk design" (~55 lines) | history of the 2026-07 design | condensed to three defects; the 2026-07-29 incident and the decisions it motivated are kept (the step-by-step Unix-semantics walk-through is dropped) |
| `KNOWN_ISSUES_TODO_BACKLOG.md` GG-2 | policy numbers KEEP_BACKUPS=1, 256M | synced to the shipped `soulmask_tmpfs.env` (3, 512M) |

**Kept, unsure** (condensed or annotated, not deleted): the "Migration from legacy" procedure and the
legacy-vs-unified table in `SOULMASK-TMPFS.md`; the July baseline and cap numbers in the two `plan-*`
documents; the bodies of the two `GAMINGHOST-SWAP` guides.
