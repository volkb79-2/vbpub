# Controller (Opus) review: iobench docs and SPEC, revision 2 (`50492cbbb`)

The operator asked for this review before implementation starts (2026-10-21 ~03:35 UTC). It is read-only.

Scope: README, DESIGN-GUIDE, SPEC and CONSUMERS (§0–§4 read in full, the rest skimmed).

Viewpoint: does the SPEC measure what our use cases need, and would its conclusions be true on the disks we have? The Sonnet review (`review-IOB-03-P1P2.md`) checked oracle hygiene and internal consistency; revision 2 fixes those findings well. Its proof model (per-value status, the collapse table, exit 7), the target guard and the restore journal are good and stay.

**Verdict: NOT READY.** Three findings are BLOCKING. Two are about physics, one is about the primary use case.

## O-1 (BLOCKING): E1 assumes a cache steady state that 30 s rungs never reach

**The assumption.** `1/iops = (C/ws)/F + (1 − C/ws)/R` holds when the cache holds a uniformly random C-sized subset of the rung's range. That is the steady state of an LRU-like cache under uniform random access to `[0, ws)`.

**Why the rungs can't reach it.** A 30 s rung reads about 6–12 GB, against a cache of about 265 GiB. The cache content during a rung is therefore whatever the **fill and the earlier rungs** left there, not the steady state.
- **Write-allocating cache:** after a sequential fill of `[0, 345 GiB)`, the cache holds roughly the last-written `[80, 345) GiB`. A rung over `[0, ws)` then hits `max(0, ws − 80)/ws`, not `C/ws`.
- **Read-allocating cache:** the hit ratio depends on which pages earlier rungs touched. The ladder starts at offset 0, so every rung re-reads the front.

**Consequence.** Both cases produce a convex, decreasing ladder that E1 can fit with r² ≥ 0.97, so a wrong model can pass "good". A model can disagree with its fixture and still look right, because FakeFio generates its scenarios from the same `C/ws` formula. Then `cliff_modeled` certifies an R that is wrong by the shape of the fill order.

**Fix (the better third version: direct measurement instead of extrapolation):**
1. **E-cold, the cold-region probe (new, primary).**
   - **What it runs:** right after the fill, before any rung touches the front, random 4 KiB read at QD32 for 30 s, confined to the **earliest-written** region `[0, cold_len)`, with `cold_len = min(32 GiB, 0.1 × WS_max)`. Then a QD1 run on a disjoint sub-region of the same cold region.
   - **When it counts as cold:** if the fill wrote more than the cache holds, this region is cold under every allocation policy (write-allocate LRU/FIFO, or read-allocate).
   - **If it is miss-like:** E2 on its QD1 run shows unimodal miss-like latency. Then `R_cold` is a **direct** cache-free rate, and `cache_state = cold_measured` is `proven`.
   - **If it is hit-like** (the cache is larger than the fill): `unproven: device_smaller_than_cache_margin`.
   - **Anything else:** `undetermined`.
   - It reads only data the fill wrote, so PLA-08 holds.
2. **E2 cross-check.** E2 stays: the latency split on the largest rung, with `R_E2 = iops(QD32) × (1 − h2)`. It is history-independent, because it classifies each IO, not a model. `proven` additionally requires `R_cold / R_E2 ∈ [0.7, 1.43]` when both exist.
3. **E1 demoted to informational.** It is a cliff/size estimate that feeds `cached_tier_factor` and the margin check, never a cap basis. Keep the fit and its degenerate-case fixtures; drop it from `proven` paths.
4. **Ladder order, made part of the record.** Each rung stores what was written or read since the fill (bytes per region). FakeFio's cache model must simulate **content** (an LRU over 1 GiB buckets, with a write-allocate flag), not the closed formula, so that a test can tell the history-dependent result from the formula.
   - **Required fixture:** a write-allocating cache with the fill-order history. On it E1 fits "good" but is wrong; E-cold gets R right.
5. **Budget.** E-cold costs 30 s + 60 s per direction, about 3 min, which fits inside the 200 s slack. The densification rungs can drop from 4 to 2.

## O-2 (BLOCKING): the write direction has no physically valid cache proof

**The problem.** A write-back cache is not a working-set phenomenon. It absorbs writes until its dirty capacity fills, then drains at the backing rate. `C/ws` hit ratios and read-style latency modes do not describe it: write latency bimodality means "acked" versus "throttled", not hit versus miss. The SPEC applies E1/E2 "per direction".

**Fix.** The write cache proof is the **time** axis:
- **Proven condition:** a write series is `proven` cache-free when the observation run (`IOB-CAL-05`) reached `steady`, and the bytes written **after** the knee are ≥ 2 × the burst bytes (`burst_units × 4 KiB`) and ≥ 120 s.
- **Fresh vs rewrite:** apply the same rule to the fresh-vs-rewrite cells.
- **Otherwise:** `unproven: short_observation`.
- **Scope of E1/E2/E-cold:** reads only.
- **Write basis:** stays `min(rewrite, fresh)`.

**Oracles:**
- a burst-then-throttle scenario where the burst is longer than the observation: must be `unproven`;
- the gstammtisch-shaped scenario (2 GB/s for N s, then 14 MiB/s): must be `proven` only after 2 × burst.

## O-3 (BLOCKING, use case): no way to calibrate the hosts we actually tune

**The gap.** The primary consumer is the mdt io.cost wizard on production hosts like gstammtisch. D-IOC-4 says the wizard runs the benchmark by default. Those hosts have no spare raw partition, but `IOB-TGT` accepts only a dedicated raw device or partition. As specified, the D-IOC-4 default can never run where it matters, and the wizard falls back to configured values every time.

**Fix.** Add target kind `file`:
1. **The file:** preallocated with `fallocate`, then fully written, on a named existing filesystem. O_DIRECT; never on tmpfs, ramfs or overlay.
2. **Guard:** identity is (filesystem UUID, inode, size). The marker sits in the file's last MiB, plus a sidecar `.iobench-target`. TGT-04 re-verifies before every writing stage.
3. **Fresh extent:** made with `FALLOC_FL_PUNCH_HOLE` instead of `blkdiscard`, or `not_measured` where the filesystem refuses it.
4. **Record honestly:** `target.kind = "file"` in the identity, and a `limits[]` entry `filesystem_layer`. File-backed values carry the extent-mapping and journal overhead and are never mixed with device-kind history.
5. **Sizing from free space:** `WS_max = min(0.85 × free − margin, R_est × fill_budget_s)`. On gstammtisch that is a few GiB, so the cliff is unreachable and read caps are `unproven: device_smaller_than_cache_margin`. That is honest and acceptable: reads are cheap there. The write cap is what matters, and O-2 can prove it.
6. **`R_est` on a burst-capped disk.** On gstammtisch the 20 s probe sees the burst (the generator saw 2 GB/s). The fill must re-plan when its own rate falls below 0.5 × `R_est`. CAL-02 already shortens the extent; say explicitly that `WS_max` is re-derived from the measured fill rate.
7. **Docs:** CONSUMERS gets a gstammtisch example with the quiet-window flags.

## O-4 (SHOULD): pricing the read and write directions consistently (vrate is one scalar)

**The tension.** DER-05 bases the model on the worst plateau per direction. With O-1 the read basis becomes the cache-free `R_cold` (about 9.8k on r1002), while real hot sets hit the cache (about 92k). vrate is a single multiplier for both directions. Two things follow:
- `max` must leave room for the read cache. Otherwise vrate pins at `max` whenever reads are cache-served, and io.cost under-admits reads by up to about 9×.
- A read/write shape error makes vrate oscillate under mixed load, as guide §14 already notes.

**Decision (controller, IOB-D-015):**
- The model keys of both directions use the proven worst basis (D-IOC-1/2).
- `max` takes the read best as `cached_ref`, which is the only permitted use of a `cached_tier` figure. List it explicitly as an exception in DER-08.
- The result records `read_cache_headroom = cached_ref / R`.
- The DESIGN-GUIDE explains why: "price the worst case, let vrate climb into the cache".
- On r1002 this gives `max` ≈ 1035; the io.cost QoS max range allows it. Add the oracle.

## O-5 (SHOULD): a hypothesis worth testing in P4

r1002 fresh-block random writes ran at 3.6k IOPS × 4 KiB ≈ **14.7 MB/s**, which is the order of gstammtisch's moving write ceiling (6–18 MiB/s, flat in bytes). If the provider's slow path is the **allocation** path, three things follow:
- the "moving" ceiling could be the share of writes that land on fresh (never-written or discarded) blocks;
- `fstrim.timer` or swap `discard` on production hosts would *reset* blocks to fresh and lower the ceiling after each trim;
- swap-outs to discarded slots would be the slowest writes there are.

**Ask:**
- S6 already has the fresh sequential 1 MiB cell. Add an info check `fresh_write_bps` and the comparison to rewrite at 1 MiB.
- P4 adds one cell: rewrite → discard the same range → write again. That shows whether the penalty is re-armed by discard.

This is labelled [I]. If it holds, it changes host settings (`fstrim`, swap discard) more than any io.cost knob. The controller will read the gstammtisch trim and swap-discard state read-only (not a test).

## O-6 (SHOULD): E2 resolution and the instrument

**Resolution.** The 19-point percentile ladder resolves the split position only to the ladder spacing: 0.1 between p50, p60, p70 and p80, which is where r1002's h sits (0.6–0.77). The agreement tolerance is 0.15, so one ladder step uses most of it.

**Instrument.** Use fio's full latency histogram (`--log_hist_msec` with `--log_hist_coarseness=0`, i.e. all bins). Compute h as the CDF mass below the valley between the modes. Keep the percentile ladder only as a summary.

**Thresholds.** The bimodality thresholds (gap ≥ 2.5×, side spread ≤ 2.0) are guesses until P4. If the backing store is local NVMe behind a host page cache, the miss is about 100–300 µs against a 110 µs hit, which is overlapping by this rule and makes everything `undetermined`. So:
- P4 stores the raw histograms in the program dir;
- the thresholds are a P4 deliverable, labelled [I] in the SPEC until then.

**Adjacency.** R_E2 multiplies a QD32 rate by an h measured in a separate QD1 run. Require the QD1 run to follow its QD32 run immediately on the same rung, and record both timestamps.

## O-7 (SHOULD): what r1002 can and cannot prove (P4 plan)

r1002 rewrite writes run at about 86 MB/s; gstammtisch runs at about 14 MiB/s with a moving cap. Different tier, different behaviour.

**P4 on r1002 proves:**
- the mechanics: guard, journal and restore, sysfs and sysctl round trips, `check`;
- the cache method: E-cold, E2, and the fill-order history;
- the fresh/rewrite physics.

**It does not prove the gstammtisch io.cost numbers.** Those come from:
- (a) a file-target calibration on gstammtisch, after O-3, inside an operator-confirmed quiet window. That waits until the host is a controlled environment again (operator: tests on gstammtisch deferred);
- (b) the long-term ceiling history (D-011 / OI-2).

**Ask:** state this split in SPEC §20 and in the P4 brief, so P4 cannot be reported as "io.cost model validated".

## O-8 (NIT)

- The `calibration.json` example uses `cliff_bytes` = 300 GiB while the text says 250–300. Fine, but make it one of the recorded values.
- README §Quick use guards with `--expect-partuuid 7b2d0c1e-07`, a GPT PARTUUID suffix form. Say "(MBR-style PARTUUID shown)", or use a GPT UUID.

## What stays as is

These rules stay as is:
- per-value status and the collapse audit;
- `check` exit semantics;
- the target guard (raw kind);
- the restore journal that never clobbers a newer value;
- the reference cells for the moving ceiling;
- metering needs both tests;
- the slow-disk time-bounding;
- the allotment table;
- the never-`io.max` rule.

## Required before P3 starts

1. Fix O-1, O-2 and O-3 in the SPEC, DESIGN-GUIDE and CONSUMERS, each with oracles and fixtures. Include the FakeFio content-based cache model.
2. Fix O-4, O-6 and O-7. For O-5, add the check and the P4 cell.
3. Re-budget `standard` with E-cold and the reduced densification. It must still fit 3600 s on r1002.
4. A short re-review by me, limited to the changed requirements.

## Addendum to O-5: gstammtisch state, read-only (2026-10-21 ~03:47 UTC)

- **Weekly trim.** `fstrim.timer` is enabled and runs weekly. The last run (2026-10-19 01:27 CEST) trimmed **341.8 GiB on `/`**, which is all the free space, plus 310 MiB on /boot. The next run is 2026-10-26.
- **Swap discard.** Swap is `/dev/vda6` and `/dev/vda7`, 34.5 G each with 7.3 G used each, `pri=10`, and **`discard=once`** in fstab. That discards the whole swap device at swapon.
- **Consequence, if the fresh-block penalty seen on r1002 applies to this provider [I]:**
  - **Swap:** every swap slot not yet written since boot is a fresh-block write. The swap allocator walks clusters through the device, so most swap-outs so far landed on such slots: ~14.6 G used of 69 G, and zswap wrote back 31.5 GB in its lifetime.
  - **Root:** after each weekly trim, every new file block on `/` is fresh too.
  - Either would make swap-out storms and saves run at the slow fresh rate. On r1002 that rate is ~14.7 MB/s at 4 KiB, which is gstammtisch's observed ceiling class.
- **Not changed.** Host tests are deferred, and these settings are the operator's call. The P4 cell rewrite → discard → write on r1002 is the test of the mechanism. If it holds, the candidate host change is to stop discarding swap and to run trim less often or not at all. That would be a docs-backed proposal for the operator, not something iobench does.

## Re-review of `31358d690` (controller, 2026-10-21 ~04:05 UTC)

O-1..O-8 are addressed well. E-cold is placed first, the touch map is recorded, FakeFio models cache content, E1 can never confer `proven`, writes are proven on the time axis, and the r1002 scope statement is in §20.

The two-tier fixture is accepted as the required fixture, even though its leave-one-out error of 0.137 sits close to the 0.15 limit. Its purpose is to show that E1 can be "good" and still wrong. The test asserts exactly that, so if the E1 rule changes, the fixture must be regenerated. Record its generator parameters in the fixture header.

**Verdict: READY after the four fixes below.** The controller will verify them from the diff; no further full round is needed.

### R2-1 (BLOCKING): a flat line is an upper bound, not a cache-free rate

**Where.** `IOB-CCH-05` row `rate_cap` proves the read cache state when "the cold probe **or the largest rung**" is flat-line. `IOB-CAL-06` calls a flat-line sequential cell "itself a proven upper bound".

**Why it is wrong.** A provider limiter can bind on *cached* data. r1002's sequential read of exactly 4096 MiB/s is such a case. Cold reads can be slower than the cap.

**Why it matters for caps.** For dev caps, too high is the unsafe direction, because the cap then never binds and production is unprotected. So an upper bound must not certify a cap basis.

**Fix:**
- `rate_cap` is `proven` only when the **cold probe itself** is flat-line, or the cold probe is miss-like and within tolerance of the flat line.
- A flat line seen only on the largest rung is a recorded fact, with no proof value.
- BPS keys: a flat-line sequential cell gives `bps = cap × cache_ratio` when the read cache state is proven. Otherwise `unproven: cache_residual_possible`.
- Remove "flat-line is a proven upper bound" as a basis proof everywhere, including `flat_rate_cap` fixture #5 for reads.
- Add a controlled-wrong fixture: the largest rung is flat at 24k, the cold probe is miss-like at 9.8k, and the cap must be 9.8k-based.

### R2-2 (SHOULD, safety on production hosts): the file target size is the operator's, never derived up to free space

**Where.** `TGT-06` takes `--size`, but `TGT-10` derives `WS_max` and the file size from `0.85 × free − margin`.

**Why it matters.** On gstammtisch `/` has ~342 GiB free. With the burst-inflated `R_est`, the plan would fill hundreds of GiB of a production root filesystem.

**Fix:**
- `--size` is required and is the upper bound: `WS_max ≤ size − fresh − tail`.
- On the root filesystem, refuse a `--size` above 10 % of free space unless `--allow-large-root-target`.
- Re-check free space before the fill, and abort the fill if free space falls below the margin while it runs. Production writes keep going during it.
- Oracle: the gstammtisch geometry (342 GiB free, `--size 40G`) gives `WS_max ≤ 40 GiB − fresh − tail`, and a 60 GiB request on root is refused.

### R2-3 (SHOULD): "fresh" in a file target means something different

**The problem.** A hole punched in a file frees filesystem blocks. The next write allocates *some* free filesystem block. That block is provider-fresh only if it was discarded since it was last written, by an `fstrim` or a `discard` mount.

**Fix:**
- For kind `file`, label the measurement `fresh_fs_alloc`, not `fresh`, and add `limits += fresh_semantics_filesystem`.
- Record the filesystem's discard mount option.
- Never use it in the write cap basis `min(rewrite, fresh)` unless the filesystem is mounted with `discard`. Otherwise it is informational only.
- iobench never runs `fstrim` itself: that is a host-global action.

### R2-4 (SHOULD): derivations from the ladder

- **`ratio_r` for `iocost_qos.max`:** use the **maximum steady over all rungs**, not `cached_ref` (the first three rungs). In `fill_history_inversion`, `cached_ref` equals the miss rate, which would give `max` no read headroom. Rename the exception in DER-08 to "the best observed read rung" and keep it the only permitted use.
- **Unproven read value (`CCH-06`):** use `min(measured_largest, R_cold)` when a cold probe exists, even when it is unclassifiable. It is the most conservative honest number. `R_E1` stays only for the case without a cold probe.

## Verification of `651a99ea4` (controller, 2026-10-21)

R2-1..R2-4 are fixed as asked. Both deviations are accepted:
- The 30G accepted / 40G and 60G refused oracle is correct arithmetic under the 10 % rule.
- The stricter `rate_cap` (a flat line on a hit-like cold probe is not proven) is right, for the same reason as R2-1.

**Verdict: READY. P3 (implementation) may start from `651a99ea4`.**
