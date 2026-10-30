# Synthetic: reproducing the zswap burst writeback stall in a VM

**Status: designed and implemented, NOT RUN.** The scripts were checked for
syntax only (`bash -n`, `shellcheck -S warning`, `py_compile`). The controller
grants the run slot. Nothing here has been observed to work end to end; the
first run will shake out harness details (see "Known weaknesses").

**Safety (the actual guard).** Runs only inside the QEMU/TCG guest of
[`scripts/debian-install-v2/testing/vm`](../../scripts/debian-install-v2/testing/vm/README.md)
(its own kernel; `swapon`, sysctls and memory hogs never touch the host).
"Is a VM" proves nothing: gstammtisch, the production host, is itself a KVM
guest and `systemd-detect-virt` says `kvm` there too, so it is **not** used.
The guard is a **positive marker**: `/etc/zwg-harness-guest`, owned by root,
containing exactly `zwg-harness-guest run=<run> nonce=<nonce>`. Only
`run-synthetic.sh` writes it, over ssh, into a guest it has just started with
`vmctl`, and it passes the same `ZWG_RUN`/`ZWG_NONCE` to each guest script.
`guest/lib-guard.sh::require_harness_guest` is the first action of
`setup-guest.sh`, `run-variant.sh` and `ensure-kernel.sh`; without the marker
(or with a mismatch) they exit 97 before changing anything. Independently, they
refuse if `/proc/swaps` lists any area other than `/swapfile`, and nothing uses
`swapoff -a` (only `swapoff /swapfile`). The marker is removed before the base
image is snapshotted. Copying these scripts to a production host does not make
them run there: someone would have to create the marker by hand.
`check-guards.sh` is the static refusal test (grep/awk only, executes nothing of
the synthetic): it fails if a mutating script lacks the guard call or calls it
after its first mutating command, if `swapoff -a` or `systemd-detect-virt`
appears, if the marker/nonce/foreign-swap checks are removed from the lib, or if
the driver stops writing the marker. It was exercised both ways (passes on the
tree, fails on a copy with the guard call deleted). The three Python helpers
(`workload.py`, `bgpressure.py`, `sampler.py`) carry the same marker check and
exit 97 without it. **Limits:** `check-guards.sh` is not yet part of any gate lane
(run it by hand), and its mutation regex misses writes made through variables
(for example `echo x > "$path"` or `$cmd`), so it is a tripwire, not a proof.

## What it reproduces

The operator's claim ([../README.md](../README.md)): with no other load started,
a few minutes after the game server settles, the zswap shrinker writes the
whole cold pool to disk at once and the game stalls; reproducible. The
synthetic builds the same *shape* at guest scale and asks which variable moves
the burst.

Host-to-guest scale model (everything derived or recorded in `meta.json`):

| Quantity | Host (2026-10-20) | Guest default |
|---|---|---|
| RAM | 16 GiB | 3 GiB (`MEM_MB`) |
| Game anon working set | ~6.6 GiB | 1400 MiB (`WS_MB`) |
| Share of it in the zswap pool at settle | ~30 % (780 MB pool at ~2.6x) | 30 % (`COLD_PUSHED_PCT`) |
| Hot subset touched regularly | not measured | 10 % at 1 Hz |
| Background pressure | dev work, constant | ballast + 24 MiB/5 s churn (`bgpressure.py`) |
| Swap device write cap | 14 MiB/s (iostat) | 4 MiB/s (`THROTTLE_MIBPS`), QEMU `block_set_io_throttle` on the whole disk |
| Swap queue | `nr_requests` 16, `max_sectors_kb` 64 | same (`setup-guest.sh`) |
| `min_free_kbytes` | 67584 KiB | the same proportion of RAM |
| `watermark_scale_factor` | 50 at the incident | 50 (H3 variant: 200) |
| Protection chain | `wings.slice` floors above the game scope | `zwg` parent's `memory.min/low` = children's sum, re-set on every band change (see below) |
| Band after the squeeze | min 4000M / low 5000M / high 8000M of ~7.5 G | 53 % / 67 % / 107 % of the settled usage `U` |
| zswap | zstd, `max_pool_percent` 25, shrinker Y, `memory.zswap.writeback=1`, `memory.zswap.max=max` | same |

At 4 MiB/s a ~1 GiB logical pool takes ~4 min to write back, about the 146 s
the host needed for ~2.1 GB at 14 MiB/s (the ratio of times, not the absolute
rate, is what matters; override with `THROTTLE_MIBPS`).

## Files

| File | Role |
|---|---|
| `run-synthetic.sh` | host driver: `prepare` (7.x-kernel guest image), `run <variant> <rep>`, `matrix`; applies and verifies the QEMU write throttle |
| `guest/ensure-kernel.sh` | installs a trixie-backports kernel (Debian 13's 6.12 has the older shrinker, not the v7.2 one analysed) |
| `guest/lib-guard.sh` | the harness-guest marker check (above) |
| `check-guards.sh` | static refusal test for the guard |
| `guest/setup-guest.sh` | guest preconditions, zswap/queue/THP settings, swap file, cgroups; fails loudly |
| `guest/run-variant.sh` | one variant end to end: fill, settle, optional squeeze, wait for burst or cap; writes `meta.json` |
| `guest/workload.py` | the "game": compressible anon WS, hot subset, own-thread churn, latency probe process |
| `guest/bgpressure.py` | constant low global pressure: mlocked ballast + periodic churn |
| `guest/sampler.py` | 1 Hz counters (`samples.jsonl`) + 10 Hz FULL kernel stacks of the game's D-state threads and of the `kswapd` threads (`stacks.jsonl`, field `kind`) |
| `analyze.py` | the authority for all definitions and verdicts below |

## Protection chain: why the `zwg` parent carries min/low

The kernel clamps a child's effective protection by its parent's. In
`page_counter_calculate_protection()` (`mm/page_counter.c:445-463`, v7.2) a cgroup
whose parent is the root gets `emin = min`, `elow = low` (445-448); a deeper cgroup
gets `effective_protection()` (`page_counter.c` 358-359 and following):
`protected = min(usage, setting)`, and if the siblings' combined protected usage
exceeds the parent's effective protection the child receives
`protected * parent_effective / siblings_protected`. `zwg` is a child of the root
and starts with `memory.min = memory.low = 0`, so `parent_effective = 0` and the
effective protection of `zwg/game` and `zwg/bg` is `0` whatever is written on
them: the PRE/POST bands and the ballast protection would be silent no-ops
(H2 could not show an effect even if one existed). `run-variant.sh` therefore
re-sets `zwg/memory.min = game.min + bg.min` and `zwg/memory.low = game.low +
bg.min` after every band change (`set_parent_protection`); then
`parent_effective >= siblings_protected` and the children keep their full
settings. This mirrors the real host, where `wings.slice` carries floors above
the game scope. The ballast is additionally `mlock()`ed (unevictable, independent
of any protection), and each second the sampler records `bg` `zswapped` and
`memory.swap.current`; a non-zero value invalidates the run.

## Measurements per run

- **Time from settle to the writeback burst** (`t_burst_s`).
- **zswap pool size and written-back pages over time** (`samples.jsonl`). Settle and
  burst use the **game cgroup's own** `memory.stat` (`zswapped` / 4096 as stored
  pages, `zswpwb` as written back), not the global debugfs counters, which also
  count any other cgroup's pages. The debugfs values are recorded alongside.
- **The workload's own stall**: `probe.jsonl`, one row per second from a probe
  process in the game cgroup that allocates a small region on every iteration
  (so it stalls when reclaim hits an allocating thread of this cgroup) and
  reads hot pages: `p99_ms`, `max_ms`, `over_200ms`.
- **The caller of the writeback, as an explicit measurement.** The host's
  bpftrace probe used `kstack(10)`, so every recorded stack ends at
  `submit_bio_noacct_nocheck < shrink_memcg_cb < __list_lru_walk_one <
  list_lru_walk_one` and the reclaim entry point above it is NOT known. Here
  `sampler.py` reads `/proc/<pid>/task/<tid>/stack` (the full stack, no depth
  cut) every 100 ms for every game thread in D state, and `analyze.py` classifies
  each stack that contains `shrink_memcg_cb`:

  | Class | Frames that decide it |
  |---|---|
  | `memcg-high` | `__mem_cgroup_handle_over_high` / `reclaim_high` (memory.high reclaim at user-mode return) |
  | `global-direct` | `try_to_free_pages` (global direct reclaim from the allocator slow path) |
  | `memcg-charge-or-max` | `try_to_free_mem_cgroup_pages` without the memory.high frames |
  | `zswap-max-store` | `zswap_store` -> `shrink_memcg` (the `memory.zswap.max` path) |
  | `kswapd` / `zswap-shrink-worker` | `balance_pgdat` / `shrink_worker` (not game threads; expected absent) |

  The run's **caller** is the class holding >= 50 % of >= 5 such samples in the
  burst window, else `mixed`, or `unmeasured` with fewer than 5 samples. A
  sampling gap (D-state sampling only sees blocked threads; a running
  decompress is invisible) is a limit, reported as `unmeasured`, never guessed.
- Global-reclaim counters: `allocstall_*`, `pgscan_direct`, `pgscan_kswapd`
  deltas over [settle, burst], and the game cgroup's `memory.events high`.
- **Where writeback runs**: samples of `shrink_memcg_cb` stacks per thread kind,
  `game` vs `kswapd` (`kswapd_shrink_samples`), so H3's premise (writeback
  moved to kswapd) is measured, not inferred from the stall alone.
- **Ballast integrity**: `bg` `memory.stat zswapped` and `memory.swap.current`
  are recorded each second; a run where either is non-zero is invalid.

## Definitions (exact)

- **Settle** (`T_settle`): started after the pool is populated (fill done). The
  first second at which, over the previous 60 s, `stored_pages` changed by <= 1 %
  and `written_back_pages` rose by <= 2 % of `stored_pages`.
- **Burst**: the earliest `t >= T_settle` with
  `written_back_pages(t+60 s) - written_back_pages(t) >= 0.25 x stored_pages(T_settle)`.
  `t_burst_s = t - T_settle`. No burst by the cap (`CAP_S`, default 2400 s after
  settle) means "no burst".
- **Stall seconds** (`stall_s`): count of seconds in `[T_burst, T_burst+360 s]` (the 60 s burst window plus the sampler's 300 s post window; the caller is read over the same window)
  whose probe `max_ms >= 200` (`STALL_MS`). `baseline_stall_s`: the same over the
  120 s after settle.
- **Valid run**: `meta.json` (outcome `ok`) and the settle marker exist and the
  ballast was never swapped; anything else is invalid and excluded, never counted
  as "no burst". A run whose pool never settles within `SETTLE_TIMEOUT_S`
  (default 900 s after the fill) is recorded with outcome `did-not-settle` and
  reported as such (distinct from "no burst").

## Variants and the matrix

| Variant | Global pressure | Bands | `watermark_scale_factor` | shrinker | Runs | Purpose |
|---|---|---|---|---|---|---|
| `base` | constant low (ballast + churn) | PRE (min=low=0.95U, high 2U) | 50 | Y | 3 | H1 |
| `idle` | only during fill, none after | PRE | 50 | Y | 1 | H1 null control |
| `noshrink` | as `base` | PRE | 50 | N | 1 | control: the shrinker is the writer |
| `squeeze` | as `base` | PRE -> POST (53/67/107 % of U) at settle | 50 | Y | 3 | H2 |
| `squeeze-minlow` | as `base` | PRE -> min 53 %, low 67 %, high unchanged at settle | 50 | Y | 3 | H2 (isolates min/low) |
| `wsf200` | as `base` | PRE | 200 | Y | 3 | H3 |
| `highonly` | none (only `memory.high` = settled footprint) | min=low=0, high=target | 50 | Y | 3 | H4 |
| `highonly-noshrink` | none | as `highonly` | 50 | N | 1 | H4 control |

18 runs. Wall time per run is the fill (the TCG emulation makes zstd
compression slow: **unmeasured**), the settle (>= 60 s) and up to the 2400 s cap
plus 180 s: assume up to ~1 h per run, so the matrix is a long job. Override
`CAP_S` for a shorter first pass (a run that hits the cap is "no burst" at that
cap, so state the cap with the verdict).

## Hypotheses and exact pass/fail criteria

The code implements these in `analyze.py` (`verdict_h1` .. `verdict_h4`);
if the code and this text ever disagree, the bug is whichever is reviewed
second.

**H1: shrinker aging under constant low reclaim pressure triggers the burst after N minutes.**
- PASS: at least 2 of 3 `base` runs burst, **none of the bursting runs earlier
  than 120 s after settle**, and the `t_burst_s` values of the bursting runs
  satisfy `max/min <= 2.0`; AND `idle` (no
  pressure after the fill) shows no burst within the cap; AND `noshrink` shows
  no burst.
- FAIL: fewer than 2 of 3 `base` runs burst within the cap; or `idle` bursts
  (time alone would then trigger it, which the source says is impossible:
  investigate); or `noshrink` bursts (the shrinker is not the writer).
- INCONCLUSIVE: any `base` burst earlier than 120 s after settle (settle
  detection suspect), or spread > 2x.
- Source prediction ([KERNEL-ANALYSIS](../docs/KERNEL-ANALYSIS.md) section 2/5),
  recorded before the run: `idle` quiet (no reclaim, no shrinker visits).
  Whether `base` bursts depends on the pressure ever reaching deep priority
  **[Q]**.

**H2: lowering `memory.min/low` (the Wings squeeze) triggers or advances it.**
Applies separately to `squeeze` and `squeeze-minlow`. With `m_b` the median
`t_burst_s` of the 3 `base` runs and `m_s` that of the 3 variant runs (a run
without a burst counts as its observed time after settle):
- PASS: `m_s <= 0.5 x m_b` and at least 2 of 3 variant runs burst.
- FAIL: `m_s >= 0.8 x m_b`.
- INCONCLUSIVE: otherwise.
- INAPPLICABLE: fewer than 2 of 3 `base` runs burst (there is no reference time to
  advance; H1 did not PASS).
- Source prediction: for `squeeze-minlow` FAIL, because while the game's usage
  stays above its effective `memory.low` the shrinker gate is unchanged
  and the shrinker count ignores protection (KERNEL-ANALYSIS section 6). `squeeze`
  (which also lowers `memory.high`) may differ by adding memory.high reclaim; that is
  exactly what comparing the two variants separates.

**H3: a higher `vm.watermark_scale_factor` moves the writeback to kswapd and reduces the workload's own stall.**
Depends on the measured caller: it can only help if the caller is global direct reclaim.
- NOT-APPLICABLE (not a FAIL): fewer than 2 of the bursting `base` runs have
  caller `global-direct`. Report the measured caller instead.
- PASS: median `stall_s` of the 3 `wsf200` runs `<= 0.5 x` the median of the
  bursting `base` runs AND the median number of `shrink_memcg_cb` D-state
  samples in game threads is `<= 0.5 x` that of base (the writeback left the
  game threads; the `kswapd` counts are reported next to it).
- FAIL: stall seconds `>= 0.8 x`. INCONCLUSIVE otherwise (also when the base median is 0).
- Note: kswapd is one thread per node. If it blocks in `blk_mq_get_tag` on the
  throttled device, allocators fall back to direct reclaim anyway
  (KERNEL-ANALYSIS section 3). That is a mechanism for a FAIL, to be read from
  `pgscan_kswapd` vs `pgscan_direct` deltas, not assumed.

**H4: `memory.high` reclaim alone (no global pressure) triggers the burst once the pool ages.**
- VALID only for runs with `allocstall_* delta == 0`, `pgscan_direct delta == 0`
  and `pgscan_kswapd delta <= 1000` over [settle, burst]; fewer than 2 valid
  `highonly` runs gives INVALID (memory.high-alone was not isolated).
- PASS: at least 2 valid runs burst with `t_burst_s >= 120`, every one of them
  with caller `memcg-high`; AND `highonly-noshrink` shows no burst.
- FAIL: at least 2 valid runs show no burst within the cap; or the control bursts.
- DID-NOT-SETTLE: at least 2 of the 3 `highonly` runs never settled: memory.high
  reclaim and the shrinker act during the fill itself, so the pool may keep moving
  and give no aging window. Reported as its own outcome, not as FAIL.
- INCONCLUSIVE: otherwise (e.g. bursts with another caller).
- Source prediction **[I]**: memory.high reclaim asks for a small `nr_pages`
  (`MEMCG_CHARGE_BATCH`) and stops once the LRU scan has reclaimed it, so the
  priority tends to stay high and the shrinker's budget small. A FAIL here would
  therefore not be surprising; a burst with caller `memcg-high` would be a
  finding.

What each combination means for the project is in [DESIGN](../docs/DESIGN.md)
section 6: the recommendation does not depend on the verdicts, but the
priority of option (4) does.

## How to run (when the controller grants the slot)

```
cd scripts/debian-install-v2/testing/vm && ./run-vm-harness.sh prepare-ready-base case-b   # once
cd zswap-writeback-governor/synthetic
./run-synthetic.sh prepare
RESULTS=./results/first CAP_S=900 ./run-synthetic.sh run noshrink 1     # smoke the whole path
RESULTS=./results/full ./run-synthetic.sh matrix
python3 analyze.py ./results/full
```

`results/` is git-ignored (see `.gitignore` in this folder).

## Known weaknesses (so a PASS is not over-read)

- **`highonly` may never settle.** `memory.high` is set to the target from the start,
  so memory.high reclaim and the shrinker act during the fill and the settle
  criterion may never hold. That is a recorded outcome (`did-not-settle`, bounded by
  `SETTLE_TIMEOUT_S`), costs the run, and leaves H4 as DID-NOT-SETTLE rather than a
  false FAIL. If it happens, lower the fill pressure (a stepped `memory.high`) before
  rerunning.

- **Never run.** Expect harness fixes on the first run.
- **TCG timing.** CPU emulation makes compression, probe iterations and page
  touching much slower than the host; absolute times are not host times. The
  claim tested is about ordering and shape, not minutes.
- **The throttle covers the whole virtual disk** (QEMU cannot throttle one swap
  file), so the guest's root filesystem I/O shares the 4 MiB/s write cap. Only
  writes are limited. Journal and logs are small, but a probe stall can come
  from root-fs I/O, not reclaim: check `stacks.jsonl` before reading a stall
  as reclaim. A dedicated throttled swap disk needs a harness change
  (`vmctl` has no second-drive option); proposed in DESIGN section 6, not done.
- **The probe is Python** and shares nothing with the workload's GIL (separate
  process), but its own allocations are tiny; a stall seen by a bigger game
  thread may not be seen by it.
- **Guest kernel.** `ensure-kernel.sh` installs whatever `trixie-backports`
  carries; `meta.json` records `uname -r`. Only a v7.2-equivalent shrinker makes
  the result transferable; if the backports kernel is 7.0/7.1, diff `mm/zswap.c`
  against the v7.2 text in KERNEL-ANALYSIS first. MGLRU state is recorded.
- **Aging pace.** `bgpressure.py`'s churn is a model of "constant low pressure",
  its depth (24 MiB per 5 s) is a policy choice, not a host measurement; the host's real
  reclaim rate is unknown (the recorder had expired). If `base` never bursts,
  sweep `CHURN_MB` and `CHURN_PERIOD_S` before concluding H1 is false: that
  verdict is "not reproduced with these parameters".
