# iobench DESIGN-GUIDE (why it works this way)

This is the WHY document. [README](../README.md) says WHAT it does, [CONSUMERS](CONSUMERS.md) says HOW to adopt it, [SPEC](SPEC.md) is the normative requirement list.
The design record moved here from `vbpub-release-program-20261019/IOB-02-DESIGN.md` (now a pointer). Revision 4 (2026-10-21) folds in the P1/P2 review, the
controller rulings R-1..R-10, the controller review O-1..O-8 and the re-review R2-1..R2-4 (section 11).

Evidence labels: **[M date host]** measured, **[I]** inferred. Sources: `IO-BENCH-01-report.md` (r1002, 2026-10-20), `IO-STALL-TESTING-GUIDE.md` and
`scripts/gstammtisch-guide/HOST-IO-INTERFERENCE.md` (gstammtisch), `IOCOST-WIZARD-DESIGN.md` (D-IOC-1..4), `review-IO-BENCH-01.md` (findings B-01, B-03, P-01).

## 1. The problem: a benchmark that measures a cache

The stock io.cost generator (`iocost_coef_gen.py`, the kernel's `tools/cgroup/iocost_coef_gen.py`) runs short max-queue-depth fio jobs (sequential 128 MiB-class
and random 4 KiB, read and write) on a 16 GiB file and prints **one point estimate of six numbers**. It has no latency, no variance, no time axis and no cache-proofing.

* On a VM disk it reads the hypervisor and provider caches: r1002 gave 4.296 GB/s read, 4.220 GB/s write and ~100k random 4 KiB IOPS in both directions [M 2026-10-20 r1002];
  gstammtisch gave 2.0 GB/s write and 94k read IOPS from a disk that saturated at ~14-20 MB/s [M].
* `O_DIRECT` bypasses only the **guest** page cache. fsync and O_DSYNC are acknowledged by a persistent write cache and prove nothing. A memory balloon removes only the guest
  page cache (buffered 2 GiB re-reads fell from 63k to 16k IOPS; direct IO did not change) [M r1002].
* The provider's ceiling **moves**: 18, 16, 14 and 6 MiB/s on one day on gstammtisch, flat in bytes/s across IO sizes [M 2026-10-20 gstammtisch, guide s14].
* io.cost's `vrate` adapts the **scale** (one multiplier on every price) but not the **shape** (per-IO versus per-byte cost, read versus write, seq versus random) (guide s14).

iobench keeps the six generator output keys (`rbps rseqiops rrandiops wbps wseqiops wrandiops`) so every consumer keeps working, and fills them from cache-proof,
time-resolved data, **with a proof status on every value** (section 3).

## 2. Calibration versus matrix (controller remark, 2026-10-21)

| | Calibration | Matrix iteration |
|---|---|---|
| Frequency | once per host or storage class; again on an **identity change** or a **sustained ceiling shift** (`IOB-MAT-04`) | as often as wanted |
| Time | `quick` 12 min, `standard` 60 min, `thorough` 75 min (hard budgets; the allotment table is SPEC section 15) | 6 cells: typical 11 min, worst case 15 min; one knob A/B (A B A B): typical 4 min, worst case 8 min |
| What | ladder to the cliff, cache-state estimators, fresh vs rewrite, ceiling, S6 cost-shape grid, S7 latency curve | short cells on the calibrated working set; sync probe under writeback load; read cost; noise floor |
| Artefact | `calibration.json` | `result.json` (references the calibration by id) |

A matrix run refuses to start without a **current** calibration and never recomputes caps or the model. The calibrated written-extent map is fixed, so every cell reads
only blocks the calibration confirmed written.

## 3. The proof model: every value says how far it can be trusted (rulings R-1, R-2, R-3, R-4)

A benchmark inside a VM can never exclude a provider cache; it can only collect evidence. The earlier design had one bit (`plausible`) and one exit code, which folded
"proven cache-free", "cache residue possible", "budget-truncated" and "quick profile" into one answer, while a global failure bit rejected unaffected groups. The
review (B-3, B-4) called that out; the fix is a **per-proposal status**:

* `proven`: the basis is steady, and any cache dependence has been excluded by independent evidence.
* `unproven: <reasons>`: a value exists and is shown, with a closed list of reasons. A wizard or installer may apply it only after an explicit operator accept that shows the reasons.
* `absent: <reason>`: no value; nothing can be applied.

`check` exit 0 means only that every *requested* proposal is `proven`; exit 7 means the result is valid but some requested proposal is unproven or absent (the JSON
lists which). The audit table of real-world conditions per status lives in SPEC IOB-STA-05; a status can only be reached from its row.

### 3.1 Why a model fit cannot prove a cache absent, and what does (findings O-1, O-2)

The ladder on r1002 never plateaued: random read at 300 / 350 / 400 GiB gave 46k / 31k / 24k IOPS on a 406 GiB partition [M]. A hit-ratio model (`1/iops = (C/ws)/F + (1 - C/ws)/R`,
linear in 1/ws) fits those three points with R ~ 9.8k and C ~ 265 GiB, which is suggestive and is exactly the trap: the model assumes the cache holds a uniformly random C-sized subset of the
rung's range, the steady state of an LRU under uniform random access. A 30 s rung reads 6-12 GB against hundreds of GiB of cache, so the cache content during a rung is whatever the
**fill and the earlier rungs** left there. Replaying that with a content-based simulator (offline, while writing this revision) shows how far the formula is from content:

* a **write-allocating** cache after a sequential fill holds the *last-written* region, so a ladder over `[0, ws)` is *rising* with `ws` (about 9.8k at 64 GiB up to 31k at 345 GiB); the first rungs measure the miss rate and the "cached reference" is wrong;
* a **read-allocating** cache that starts empty gives a ladder decaying from 41.8k to ~10k that E1 rejects;
* a **two-tier** cache (40 GiB at 92k IOPS, 300 GiB in total at 15k IOPS, backing store 9.8k) yields a ladder on which E1 fits `good` (`r2` 0.977, leave-one-out 0.137) and reports **R = 14.6k, 49 % too high**. That is the required fixture (SPEC IOB-CCH-08 #1): a wrong model that passes its own acceptance test.

So the estimators are ordered by directness:

1. **E-cold (primary, direct)**: right after the fill, before anything else touches the front, read the **earliest-written region** `[0, cold_len)` (`min(32 GiB, 0.1 x WS_max)`), QD32 for the rate, then QD1 on a disjoint half for the full latency histogram. When the fill wrote more than the cache holds, that region is cold under every allocation policy (write-allocate LRU or FIFO, read-allocate). A miss-like unimodal histogram makes `R_cold` a *measured* cache-free rate: `cold_measured`, `proven`. A hit-like one (the cache is larger than the fill) is `unproven: device_smaller_than_cache_margin` or `no_cliff_hit_like_mode`. Anything else is `undetermined`.
2. **E2 (cross-check)**: the latency split on the largest rung (full histogram, h = CDF mass below the valley between the modes, `R_E2 = iops(QD32) x (1 - h)`), with its QD1 run immediately after the rung's QD32 run. When both exist they must agree within [0.7, 1.43].
3. **E1 (informational)**: the fit is kept for a cliff-size estimate and `cached_tier_factor`, with its degenerate-case fixtures, but it never decides a cache state and is never a cap basis.

A cap never exceeds the measured largest-rung rate whatever any estimator says, and a cap whose basis may still contain cache hits is never `proven` without direct evidence (IOB-STA-03).

**Writes are proven by the time axis.** A write-back cache absorbs writes until its dirty capacity fills and then drains at the backing rate; `C/ws` hit ratios do not describe it and write-latency bimodality means "acked" versus "throttled", not hit versus miss. A write series is `proven` cache-free when the observation run is steady, lasted >= 120 s after its knee, and the bytes written after the knee are >= 2x the burst bytes. Otherwise `unproven: short_observation`. E1, E2 and E-cold are read-only.

The recorded r1002 data holds no cold probe, so replaying it gives `undetermined`, an honest outcome the SPEC asserts; P4 produces the first real cold-probe histograms, and the E-cold/E2 thresholds are `[I]` until then.

### 3.2 Bandwidth caps need their own basis

The ladder is random 4 KiB, so `RBPS`/`WBPS` have no basis in it. The basis is the steady bytes/s of the sequential 1 MiB QD16 cell at the cap-basis working set, scaled by the
same direction's `cache_ratio = R / measured_largest` when that is proven (else 1 with `unproven: cache_residual_possible`); a flat-line sequential cell (r1002 read 4096 MiB/s) is a recorded fact and
never a proof (a limiter can cap *cached* data; cold reads can be slower than the cap), so its bytes/s is scaled by the same `cache_ratio`; with no such cell the BPS caps are `absent: bps_basis_missing`.

### 3.3 Moving ceilings are findings, not failures (ruling R-8)

The provider's rate moves within a day (6-18 MiB/s [M]). Comparing two numbers measured minutes apart (random vs sequential bytes, fresh vs rewrite, the metering grid) can therefore
fire without any cache. Every comparison group is bracketed by a **reference cell** (random 4 KiB QD8 on 1 GiB, 15 s, before and after, pairs interleaved `[ref, A, B, ref]`). If the reference moved > 15 %, the
comparison is `unproven: ceiling_moved`, both values are kept, and the move is appended to the ceiling history as a `reference_shift` observation. It is not a plausibility failure.

## 4. Cache-proofing: what defeats what

| Technique | What it defeats | What it does not |
|---|---|---|
| `--direct=1` | guest page cache | any host/provider cache |
| The cold-region probe (earliest-written region right after the fill), cross-checked by E2 | host/provider read caches, whatever their allocation policy, as long as the fill exceeds the cache | a cache at least as large as the fill: it reads as `no_cache_observed`, `unproven: cache_not_excluded` (R-11), because rate and latency cannot tell it from no cache |
| The time axis (>= 120 s after the knee, bytes after >= 2x burst) | write-back caches and token buckets for writes | a burst longer than the observation (`unproven: short_observation`) |
| Steady tail = median of the last 25 % of a <= 2 s series, knee/burst detection | warm-up and bursts shorter than the observation | a token bucket longer than the observation: every result records `bucket_exclusion_s<=N`; `standard` adds a 240 s write and 120 s read observation, `thorough` 900/240 s (the 0.9 -> 3.2 GB/s step at ~430 s seen on r1002 needs `thorough`) |
| Read only data **written by this calibration** (argv-confined to the recorded extent) | zero-page reads of unmapped space (99.9k IOPS on a discarded range [M r1002]) | - |
| Fresh vs rewrite series (**writes only**; reads are always of data written earlier) | provider allocation / copy-on-write (random writes to fresh space ran at 3.6k IOPS, 7 ms p50 against 71k rewritten [M r1002]) | - |
| Flat-line detection (CV < 1 % over >= 60 s => `rate_limited`) | reading a provider cap as a capability (sequential read exactly 4096 MiB/s [M r1002]) | proving a rate cache-free: an upper bound can sit on cached data. Only a flat-line **cold probe** that is not hit-like earns `rate_cap`. |

## 5. Stages and why each exists

Every stage exists because a derivation reads its output.

| Stage | Measures | Feeds |
|---|---|---|
| S0 | identity (devno, PARTUUID, size, model/serial if any), disk chain, scheduler, `max_hw_sectors_kb`, RAM, PSI, containers, every slice `io.max` (read only) | validity, refusals |
| S1 | 20 s direct sequential write to the target's first GiB: `R_est` | time-bounded sizing, slow-disk detection |
| fill + E-cold | sequential fill of `[0, WS_max)` (re-planned if its rate falls below 0.5 x `R_est`); then the cold-region probe | `R_cold`, the primary cache state |
| optional cache-evidence | after E-cold, wait in one-second abort-checkable slices, then read the last written GiB twice at QD1 | indirect delayed-read and repeat-pass hints only |
| S2 ladder | random 4 KiB read/write QD32 over a doubling ladder + two densification rungs; QD1/QD8 at the largest | cliff, `QD90`, `cached_ref`, the best read rung, E1 (informational) |
| E2 | QD1 full-histogram runs on the smallest rung (`hit_ref`) and, adjacent to its QD32 run, on the largest | cross-check of `R_cold` |
| fresh/rewrite, rearm, reference cells | 4 KiB random writes to a discarded extent vs a written one; one rewrite -> discard -> write cell (does discard re-arm the penalty? hypothesis [I]); the reference pairs | fresh penalty, `fresh_write_bps`, `ceiling_moved` |
| observation runs | 240 s write / 120 s read at the largest rung and a 150 s fresh write run (the 120 s proof rule plus a fixed 30 s margin: fio stamps a 120 s run's last sample at 119.9x s, and widening the rule with a tolerance was rejected) (`thorough`: 900/240 s) | the write time-axis proof, bucket exclusion |
| S3 drain rate | `R_drain`: device bytes/s under the reference writeback load on the work dir's filesystem | `dirty_bytes`, caps |
| S4 sweep | sync-latency probe under buffered load across candidate settings; REF twice for the noise floor | choice of `nr_requests`, `max_sectors_kb` |
| S5 read cost | direct sequential reads per `max_sectors_kb` candidate | the read-floor rule |
| S6 cost shape | 22 cells: sizes {4K,16K,64K,256K,1M} x {seq,rand} x {read,write} on rewrite data, plus fresh random-4K and sequential-1M **writes**; a fixed seeded shuffle with repeated anchors | the six model keys, metering |
| S7 latency curve | p50/p90/p95/p99 at 10/30/60/90 % of the ceiling | `rlat`/`wlat` |
| S8 ceiling history | not a run stage: append-only plateau observations (benchmark, governor, probe) per direction and workload (`seq1m`, `rand4k`) | the vrate bounds |

S6 deviates from IOB-02 section 10 ("equal offered byte rates"): the grid *saturates* every cell (bytes/s and IOPS at the ceiling are what the metering tests compare) and adds a
reference-bracketed anchor schedule instead; half-rate cells are not needed for the classification.

The S4 method is the gstammtisch A/B (`ab_runner.sh`) shrunk. Measured results that fix its design [M 2026-10-20 gstammtisch]: bounding bytes in flight removed multi-second stalls
without losing throughput (A4: probe p99 0.54 s vs 34.7 s); identical cells differ ~2x in 60 s (so a neighbour must beat REF by more than the measured noise ratio in a cell *and* its
repeat); 1 MB requests were the worst cell and 64 KB the best, at a sequential-read cost of ~80 %.

### 5.1 Metering (ruling R-9)

Byte metering is concluded only from **both** tests: bytes/s flat across IO sizes (CV <= 0.15 while IOPS varies >= 0.40) **and** sequential ~ random at equal size (within 20 % at >= 4 of 5
sizes). Flat-across-sizes alone can come from a cap that happens to bind all sizes; seq ~ rand alone says nothing about per-IO versus per-byte. Otherwise the result is `undetermined`
and the dependent model values are `unproven: metering_undetermined`. `fresh_penalty` is a separate field (a byte-metered disk with a fresh penalty is still byte-metered). An operator
may type `--metering`; the value is stored as an override and makes dependants `unproven: operator_override` (D-IOC Q4: "asked only when undecidable").

### 5.2 Fio byte limits and runtime ceilings

Fio's `--runtime` is a ceiling: a job stops when its configured workload completes or the limit expires. `--time_based` changes that behavior and makes fio repeat the workload until the full runtime elapses, even after its data region or `--io_size` goal has been reached (see https://fio.readthedocs.io/en/latest/fio_doc.html#time-related-parameters and https://fio.readthedocs.io/en/latest/fio_doc.html#i-o-size). iobench therefore emits `--time_based` only when `io_size` is absent. A byte-bounded fill chunk sets both its region size and `io_size` to the planned chunk length; it writes that range once unless the runtime ceiling or an interruption ends it early. The same rule keeps other `io_size`-bounded writes within their byte budgets.

The old builder always emitted `--time_based`. In the 2026-11-08 runs, every fill reached its runtime and reported `fill_bps / R_est = 10/41 = 0.244`; the r1002 and controller report fill figures and the dependent PLA-09 comparisons are invalid as measurements. Do not compare or reuse those values. FakeFio follows the argv: with legacy `--time_based` it loops past `io_size`, while the regression verifies the corrected fill writes its byte target in about `io_size / rate` seconds.

## 6. Derivation rules (revised by D-IOC-1..4 and the review)

Targets are explicit; defaults are *policy* defaults, never substitutes for a measured fact.

| Target | Default | Meaning |
|---|---|---|
| `W` | 2 s | worst-case sync wait behind ordered data and queue |
| `P` | 250 ms | S4 acceptance threshold for the probe's `fdatasync` p99 |
| `F` | 25 % | smallest acceptable sequential read throughput relative to `max_hw_sectors_kb` |
| `U` | 70 % | dev aggregate share (conflict C-1, operator direction guide s13) |

* **R1 `dirty_bytes`**: order R2, R3, then R1. `inflight = nr_requests x max_sectors_kb x 1024`. Largest power-of-two MiB <= `W x R_drain - inflight`, clamped to `[4 MiB, 25 % of T_eff]`; `T_eff` is the kernel threshold computed from the journal's *original* `vm.dirty_ratio` and available memory, so a value applied earlier by host-setup does not ratchet the next proposal down. `"kernel"` when the unclamped value reaches `T_eff`; flag `inflight_exceeds_budget` when the budget is already spent. R1b: background = dirty / 4 (`[I]`).
* **R2 `nr_requests`**: `2 x QD90` (write direction, largest rung), power of two in `[8, 64]`. The tested depths are 1, 8, 32 (+64), so only 8, 16 or 64 result; intermediate depths are untested.
* **R3 `max_sectors_kb`**: largest power-of-two KB <= `R_drain x 10 ms` (the 10 ms request time is `[I]`), clamped to `[32, max_hw]`, raised to the smallest candidate meeting the read floor `F` unless the sweep shows it breaks `P`.
* **R4**: the chosen triple's REF probe p99 must be <= `P`, else `unproven: target_missed`.
* **R5 dev caps**: one aggregate cap, `U x basis x min(1, worst/median)` per key; basis from the cap-basis rules (write IOPS = `min(rewrite, fresh)`); using the worst plateau makes the cap bind when the provider drops to it. No per-tier caps.
* **R6 model and QoS**: model basis = the **worst plateau** per direction (D-IOC-2). Byte-metered: `wbps = worst`, `*seqiops = *randiops = bps/4096`; otherwise from the S6 fit. `min = 100`; `max = ceil5(110 x max(best_read_rung/R, best_w/worst_w))`: both directions are priced at the worst/cache-free basis (**IOB-D-015**: "price the worst case, let vrate climb into the cache"), and `max` leaves room for reads that the cache serves, using the best observed read rung as the read best (the only permitted use of a `cached_tier` figure; `cached_ref` is not used because a write-allocating fill can make it the miss rate); the result records `read_cache_headroom = best_read_rung / R` (r1002: 9.39, so `max` = 1035); `wpct 90`, `wlat = 2 x light p90` rounded up to 5 ms in [10, 50] ms; `rpct 95`, `rlat = 2 x light p95` rounded up to 1 ms in [2, 20] ms. Histogram source: the governor if running, else S7 (D-IOC-3). Without ceiling history, worst/best come from this run's own windows and every dependent value is `unproven: worst_from_single_run`.

**Worked check against the evidence** (replay oracles): gstammtisch with `R_drain` 10.5 MB/s gives dirty 16 MiB / 4 MiB, `nr_requests` 16, `max_sectors_kb` 64 (the measured best); r1002 gives `kernel` for dirty and sectors.

## 7. Ceiling history (S8)

`ceilings.jsonl` is history, not a run result: `{ts, dir, workload, plateau_bps, duration_s, source, kind, devno?, partuuid?, note?}`, worst / median / best per `(dir, workload)` over a window; bound to the disk (section 7.1), not to a partition,
never invalidated by age. `seq1m` plateaus feed `*bps`, `rand4k` plateaus feed IOPS keys (a 4 KiB plateau is never read as a bandwidth). `recalibrate_recommended` fires when the median of
the last 12 h differs from the calibration's by > 25 % in two consecutive windows; until a probe or governor populates the file (D-011) the history is `single-run` and the result says so.

### 7.1 The key is the disk, not the partition (host-tuning review B2, 2026-11-05)

The history header used to hash `devno`, `partuuid` and the target kind. A calibration on a throwaway partition and a governor's observations of a scratch file on the data filesystem could therefore never join one history, and the result itself went `stale` the moment the partition was deleted (`live_identity` reloaded the target by path). The identity has two layers: `identity.disk` and `identity.target` say what was measured (the partition's `devno`, `partuuid`, size; a file's filesystem uuid and inode), and `identity.disk_scope` says on what disk (`model|serial|wwn|size_bytes`, plus the host for a disk with neither a usable serial nor a wwn). Only the scope is the history key and only the scope makes a result stale; the partition is a field of each observation (`kind`, `devno`, `partuuid`), and a vanished partition is the advisory warning `target_changed`.

RealHost reads a whole-disk serial from `/sys/block/<disk>/serial`, the path exposed for virtio-blk in the r1002 Debian 13 guest. It does not fall back to `/sys/class/block/<disk>/device/serial`, which was absent in that acceptance kernel.

A serial-less scope is weak. If its target is gone, `find_disk` requires the remembered whole-disk name, equal model and size, and a candidate with no usable serial or wwn; a non-null stored machine-id hash must also match the live non-null hash. A null stored hash binds no host, so an installer-chroot record remains findable after first boot even if its throwaway partition is deleted. The live scope can then have a different key; consumers read `identity.disk_scope.key` from the stored identity instead of recomputing it. A known stored hash becoming missing or `uninitialized` is stale. A weak stored scope that now resolves to a strong disk is stale, since a new serial does not prove that the physical disk is the same. Virtual defaults such as QEMU's indexed `QM00001` through `QM99999`, the `QEMU HARDDISK` model, VirtualBox's `VB` plus two eight-hex groups, `vdisk` plus a decimal index, zero strings, repeated-pattern strings and common placeholder values are treated as missing identifiers by `PLACEHOLDER_DISK_IDENTIFIER_PATTERN`. The host hash still assumes machine-id uniqueness; cloned templates can violate that assumption (IOB-BL-25). A same-name, same-model, same-size weak replacement on the same host remains indistinguishable by construction (IOB-BL-21).

The evidence-class table separates **stability** from **value derivation**. Raw (R) observations can confirm read and write stability and supply model/cap values. File (F) observations can confirm write stability only; probe (P) observations can confirm read stability only. F and P rows cannot supply model/cap values; a file read is never cache-free and a probe never writes. The ordinary history summary and `check.history` use the same stability-eligible rows, while proposal derivation uses raw rows only. iobench still proves nothing from history (IOB-STA-03); the table is the contract the layer above builds its promotion rules on.

### 7.2 The veryquick profile

An installer needs a useful identity and sanity check at a cost of about three minutes. The retained IB-VQ evidence measured eight runs at 188–189 s against the earlier 173 s plan. The updated plan adds a 16 s startup/reap allowance to S0 and lengthens the sequential-write cell by 12 s, for 201 s total and a 210 s hard budget. That total also equals the measured upper bound plus the longer cell's 12 s; the timing test uses those independent wall-clock observations rather than injecting sleeps equal to the allowance. The quick plan's 18 s allowance is derived from all four retained observations: r1002 took 626 and 688 s, v1001 took 627 and 627 s, and the maximum excess over the 670 s base plan was 18 s. A 4% forecast tolerance gives 715.52 s, within the 720 s hard budget. Live timing has not been repeated after either plan change.

The eight IB-VQ runs showed that the cold-region class was not repeatable on the same disk. `veryquick` therefore reports `cache_state: undetermined` and `class: not_assessed`, while retaining the raw observation as `diagnostic_class`. That profile can decide disk identity, basic plausibility/sanity, and read/write IOPS. It cannot decide cache class, establish a cache-free cap or model, or provide tuning values. Every cap and model value is `unproven: veryquick_profile`; the model is a two-size coarse formula (`bps` from the 128 KiB sequential rate, `randiops` from the 4 KiB random rate, `seqiops = bps // 4096`), not a fit. A closing reference cell still does not fit; the plan does not claim to detect a moving ceiling.

Raw-device marker initialization is supported, but RealHost refuses raw-device write and discard stages. The fio command opens a path itself and `blkdiscard` has no way to use iobench's held descriptor, so neither can retain a kernel `O_EXCL` claim for the full mutation interval. The smallest safe behavior is to disable raw mutation until a claimed writer exists. The marker's `O_EXCL` open protects only that marker operation. At a raw writer boundary, the `O_RDONLY|O_EXCL` probe detects a Linux block-device holder conflict, such as a mounted filesystem or another exclusive holder; an ordinary non-exclusive opener does not make it fail. The probe closes immediately and does not reserve the device. `--allow_mounted_write=0` stays as an additional mounted-device guard and does not exclude ordinary process openers. Use a preallocated file for benchmark runs. On btrfs, file targets and matrix work dirs are accepted only when `/sys/fs/btrfs/<uuid>/devices` identifies exactly one backing device; multi-device or unreadable member lists are refused because one device number would misrepresent a multi-device filesystem. Root and namespace checks use the resolved source identity and retain the raw mountinfo number to match aliases. File results include extent mapping, filesystem allocation and copy-on-write effects, so use them as filesystem-layer observations and do not compare their absolute rates directly with raw-partition results.

`fallocate` reserves a file extent; it does not write the bytes. The fill and each later write cover only their planned ranges, so a file target can retain a large allocated-but-unwritten gap. A read there may be answered from the filesystem's zero page without a device request, which would inflate an IOPS measurement and say nothing about the disk. `target init` and `target show` therefore expose the usable extent as `usable_bytes`, the logical data-extent coverage as `written_bytes`, and the remaining gap as `unwritten_bytes`. RealHost derives this value with `SEEK_DATA`/`SEEK_HOLE` only for ext4, XFS, and single-device Btrfs, clipping to `[0, usable_bytes)`; FakeHost models the same extent map. It is not physical allocation, nonzero content, or a count of fio-written bytes. The query cost follows the number of reported extents (about two metadata seeks per extent); a fragmented file can take more seeks. Other filesystem types return a `null` number and an explicit `written_bytes_status`; tmpfs remains refused as a target. A raw device has no file extent map, and a dry run does not create one. Failed seeks are errors, never a fallback to file size or allocated-block count.

Targets share one minimum-size rule: 1 GiB usable plus the reserved 1 MiB marker tail. `target init` refuses an undersized device before writing its marker, the file-size parser and initializer share the same constant, and calibration checks it for older marked targets. That prevents an accepted target from being rejected only at S1. The work dir must already exist and be empty: iobench reads its actual filesystem and free space before creating scratch files, so a missing path is a `workdir_missing` refusal rather than an implicitly created directory. Its free-space requirement is `max(4 GiB, 3 × planned cell volume)` after target initialization.

The `identity.virt` field is retained for schema compatibility and stays `unknown` by design. A guest-level detector such as `systemd-detect-virt` reports a virtualization layer, not the backing disk or cache behavior; iobench does not treat it as identity or evidence.

A saturating `standard` run on an idle host can itself trip io-full PSI above 25 within the first minute because fio tasks stall while saturating the device. Schedule install-time measurement before workloads start. An operator may explicitly relax `--abort-psi` for a controlled run; iobench does not silently change the stop rule. This limit is separate from whether the device is idle before the run.

IOB-PLA-19 is recorded when the sequential read/write cells are steady and comparable. In veryquick, an unsteady sequential-write cell is reported as `not_applicable: write cell unsteady (cv=...)`; the write cell has a 24 s window to reduce this case. Its inferred ratio bands need IOB-ACC-01 calibration: a ratio up to 20x passes; above 20x through 100x is `info` and makes the overall verdict inconclusive because veryquick's cold class is not assessed and cannot corroborate cache service; above 100x fails on its own. Other profiles may use a hit-like cold probe to corroborate the 20x–100x band. A veryquick `pass` does not prove that no cache exists. Every veryquick result also carries `cache_model_undetermined` because its cold class is deliberately `not_assessed`. `veryquick` refuses to replace `calibration.json` or `result.json` in a state directory containing a non-veryquick or unreadable record. Give it a separate `--state-dir` when a fuller calibration already exists.

The retained v1001 standard evidence places its single PLA-06 failure on a depth rung, most likely the QD1 or QD8 write rung on the btrfs file. It records a check value of 0.006, no `ladder.excluded_rungs`, `qd90.write: null` while read QD90 is 32, and a QD1 write p99 completion latency of 3.888 µs. The retained mean `clat` near 3.5 µs gives a QD1 clat-only ratio near 0.0056 at the observed roughly 1.6k IOPS. The 3.888 µs p99 is consistent with that small latency, but neither value establishes the missing latency component. Submission/slat time is the most likely hypothesis, but the old record does not include `slat` or total latency and cannot distinguish QD1 from QD8. At QD greater than one, blocking submission could still leave a ratio near `1/QD`, so QD8 may also fail. The evidence does rule out the previous S7 attribution: PLA-06 runs on the QD32 ladder, QD1/QD8 depth runs (and QD64 for thorough), and veryquick's random-read rung; S7 latency-curve points are never passed to the check.

Little's law must use the latency associated with the measured I/O rate. Fio's `clat` omits time in submission; for the retained btrfs result, its small measured mean `clat` and p99 leave submission time as the leading hypothesis, not an established cause. PLA-06 uses `lat_ns.mean`, or `slat_ns.mean + clat_ns.mean` when `lat` is absent. If neither usable latency mean is available, the check says `not_applicable: fio did not report lat/slat`; when the fio version is expected to emit these fields (fio >= 3.30), that absence is a measurement defect and fails the check. Each check names its job and QD and reports IOPS plus mean clat, slat and total latency. Schema minor 5 persists all PLA-06 rung rows in `quality.little_law_rungs` and the depth subset in `calibration.ladder.depth_rungs`, including incomplete-run partial records; the next live run can test the submission-time hypothesis directly. Failures are combined by unioning their degradation effects. A failed main QD32 ladder rung remains unusable for cap derivation and degrades caps. A failed QD1/QD8 depth rung only invalidates that direction's QD90, which is the only proposal group that consumes those depth observations. A single depth failure must not blank unrelated read and write caps. The replay and decision are recorded under corrected IOB-BL-27.

Fill limits keep the cache proof conservative. A time-budget truncation remains `limited-by-budget`; a free-space stop is `limited-by-space`, and fio completing fewer bytes than requested is `limited-by-fio-short`. Those distinct records prevent a partial fill from being reported as a complete working set.

### 7.3 Cache and burst observations

`capability.jsonl` is an append-only description history keyed by the same whole-disk scope and evidence class as `ceilings.jsonl`. Each completed calibration stores measured burst/knee and read-cliff observations that it actually has. `veryquick` has only a boolean `write_burst_seen`, so its numeric capability fields remain null. There is deliberately no recovery-time field: iobench does not measure recovery time. The row's confidence describes the observation's evidence class and quality; the row never feeds a coefficient or proposal.

After E-cold, standard and thorough runs can also wait, read the last written region, and immediately repeat that read. The wait checks signals and the abort guard every second. Its rate is compared with the 4 KiB QD1 rate from `ecold-qd1`; both runs use the same block size and queue depth, while the separate E-cold QD32 rate is not used as this reference. The second pass tests whether rereading the same data speeds up. Both are indirect cache clues. `O_DIRECT` avoids the guest page cache, but a guest cannot tell whether faster data came from a hypervisor cache, a provider cache, or another layer. That uncertainty is why the evidence appears as a hint in `quality.cache_state`; `status: not_measured` makes clear when neither direct read ran. Schema minor 4 records this status, the reference used, the per-geometry plan, and the partial-run and veryquick classification fields. Schema minor 5 adds per-rung PLA-06 latency evidence to result and calibration records. The hint does not alter `quality.plausibility`, the cap basis, or io.cost values. A `hit-like` hint uses the inferred 1.2x speedup rule, recorded for later calibration under IOB-BL-26. When the configured delay plus two reads does not fit the profile's remaining planned budget for the selected geometry, the stage and both cells say `skipped:budget` and the dry-run contains no read jobs for it.

`iobench capability --json` lists `raw`, `file`, and `probe` counts and the last appended row for each kind. Pass `--kind raw`, `--kind file`, or `--kind probe` to show one kind; `--since` filters observations inclusively. `latest` means last appended within that kind, so clock skew in row timestamps does not reorder the durable JSONL history. `check --json` uses the same per-kind summary and reports malformed rows in `skipped_rows`; valid older rows remain visible. A newer schema is reported as unsupported with its version and is left in place. Reading those rows never promotes or supplies values.

## 8. Code location and migration (from IOB-02, accepted)

New library `libraries/iobench`: stdlib plus `fio`, a pure core behind an injected `Host`, a thin `iobench.cli` that adopts `cli-extended` (so the **CLI** needs `cli-extended` on
`PYTHONPATH`; the measurement core does not). In-repo consumers copy the source (precedent: `install.sh` installs the generator; AGENTS "Consuming assay from inside vbpub"); out-of-repo
consumers get a cmru-released wheel with its sha256 (never PyPI; no zipapp: it could not carry the `cli-extended` wheel dependency, and in-repo consumers copy the source). New code, no GPL code.

| Phase | Change |
|---|---|
| M0 (IOB-03) | library ships, schema 1, replay tests; no consumer changes |
| M1 | `mdt-io-baseline.py` becomes a wrapper; `/var/lib/mdt/io-baseline.env` becomes a projection; `--with-baseline` calls iobench |
| M2 | debian-install-v2 `_run_io_benchmark` calls iobench (open item OI-1: installer needs a raw target plus a separate work-dir filesystem) |
| M3 | `iocost_plausibility.py`'s `rotational` rule retired |
| M4 | `iocost_coef_gen.py`, its patches and `.sha256` flow deleted |

### Registered repository gate

The R2 lane is a native Assay lane in `run-gate.toml`, rather than a command
that assembles `python -m assay.cli run` itself. Native dispatch lets
Run-Gate apply and record its R2 `jobs` policy, while Assay owns the
verdict, progress stream and candidate state contract. Run-Gate moves
resumable state under the checkout's ignored `.run-gate/` area so retries
through ephemeral worktrees retain the same campaign state.
The former command lane's state and progress paths are not imported: the
native lane starts a new campaign state under the Run-Gate storage contract.

The Assay lane declares `jobs_max = 4`; Run-Gate's `jobs = "auto"` policy
uses measured candidate RSS when available, otherwise a conservative 1 GiB
estimate, and stays at one worker unless Assay's prepared workspace confirms
its supported btrfs cheap clone. The old Assay `jobs = 1` key was a legacy
alias for `jobs_max`, not a per-run worker choice, so leaving it in place
would have capped the opt-in policy at one.

## 9. Safety design

* **Target guard (finding P-01)**: a target is usable only with iobench's own marker (the last 1 MiB), written by `iobench target init`, which refuses a mounted (checked in the caller's **and** PID 1's mount namespace), swap, root-disk or signature-bearing device unless `--i-own-this-device` is given together with the matching `--expect-size` and `--expect-partuuid` (or `--expect-serial`); a device with neither identity is refused. The marker is re-verified before **every** writing stage (fio, discard, fill) in every profile; `--installer` only waives the live-host refusals. iobench never runs `mkfs`, `mount`, `umount` or a bare `wipefs` (no builder exists).
* **Never `io.max`**: no `io.max`, `IOReadBandwidthMax=`, `set-property`, `daemon-reload`. Slice `io.max` is read before and after and a difference is exit 9 (the systemd per-spelling hazard, guide s6).
* **Restore on every exit** from a journal in `/run/iobench/restore.json` written before the first change; a knob is restored only if its value still equals what iobench applied (never clobber a newer setting); created work-dir files are removed; verified by read-back.
* **Volumes from time and measured rate**, never fixed; a truncated stage yields an honest failed stage, not a guessed value. On a slow capped disk the calibration works time-bounded from the measured cap (conflict C-2).
* **Live host**: refuse with production containers or listed production units active unless `--confirm-quiet-window` (unlisted systemd units are not detected, and the refusal text says so); abort needs `--protect-cgroup` and `--abort-psi` (no defaults); the sweep changes host-global `vm.dirty_*`.
* **Process-death scope (IOB-HOST-01)**: the parent-death supervisor belongs to asynchronously spawned benchmark commands through `_ProcHandle` (`Host.spawn` and `Host.write_range`). Synchronous `Host.run` clients do not get that signal. In particular, systemd owns the transient probe unit separately from its `systemd-run --wait` client, so killing the client does not stop the unit; normal abort handling explicitly stops it and `RuntimeMaxSec` bounds its lifetime.

## 10. Decisions

IOB-D-001..014 are those of IOB-02 section 9 and are **accepted** (controller, 2026-10-21); **IOB-D-015** (controller, review O-4): both directions use the worst/cache-free basis and `max` uses the best observed read rung as the read best, an explicit exception in IOB-DER-08. The 14 original decisions: D-001 library, D-002 io.cost moves in (phased), D-003 CLI shell adopts cli-extended, D-004 targets as
wizard keys with policy defaults, D-005 raw read-only reads opt-in, D-006 standard first run / quick re-measure, D-007 refuse unless confirmed, D-008 identity-only validity, D-009 slow disk,
D-010 never branch on hypervisor detection, D-011 long-term probe later, D-012 re-implement without GPL code, D-013 fresh-block rung, D-014 never persists.

**Conflicts with the newer decisions:**

| ID | Conflict | Resolution |
|---|---|---|
| C-1 | IOB-02 R5: `U = 40 %` and a gates tier at 1/3 vs guide s13: one aggregate cap at ~70 %, no per-tier caps | follow s13 |
| C-2 | IOB-02 D-009 "skip the sweep at R_est < 5 MB/s" vs the controller remark "time-bounded from the measured cap" | time-bounded; sweep skipped only if the gap cannot drain the dirty pool (then `formula_only`) |
| C-3 | IOB-02 R6 vs D-IOC-1/2/3 | D-IOC rules |
| C-4 | D-006 default profile vs D-IOC-4 | the wizard default is a configurable key; `standard` stays the engine default |
| C-5 | D-003 requires the cli-extended surface/review artefacts | in P3 scope (`IOB-CLI-10`); the core never imports cli_extended |

## 11. Controller rulings on the P1/P2 review (2026-10-21)

| Ruling | Where applied |
|---|---|
| R-1 per-proposal `proven`/`unproven`/`absent`; exit 7 = valid with unproven; collapse table; accept to apply; BPS own basis | SPEC section 4, IOB-CAL-06, IOB-CHK-01 |
| R-2 second independent estimator (latency modes); two must agree; degenerate cases with fixtures | SPEC section 6, DG 3.1 |
| R-3 rung sizes from the device; largest <= 0.85 x usable; PLA-02 = largest >= 1.2 x cliff else `device_smaller_than_cache_margin` | IOB-CAL-01, IOB-PLA-02 |
| R-4 no cliff is valid; miss-like unimodal => proven; hit-like => unproven (`cache_not_excluded` when the cold region is as fast as the warm one, R-11); `quick` reports unproven | IOB-CCH-04, IOB-PRF |
| R-5 "fresh" is writes only; one matrix count | IOB-CAL-07, IOB-SHP-01, IOB-SWP-01 |
| R-6 discard, fill, mkfs through the same marker guard; no installer bypass; tail oracle for every writer | IOB-TGT-03/04, IOB-ARC-05 |
| R-7 noisy-knee fixture fixed (18000/22000 alternation, CV 0.10) | IOB-SER-02 |
| R-8 moving ceiling is a finding; reference cells; `ceiling_moved` | SPEC section 8, DG 3.3 |
| R-9 byte metering needs both tests | IOB-SHP-03, DG 5.1 |
| R-10 no VM lane; assay mutation lane registered | IOB-ACC-01, IOB-GATE-01, `run-gate.toml`, `assay.toml` |
| O-1 cold-region probe primary, E2 cross-check, E1 informational; FakeFio simulates cache content; E1-good-but-wrong fixture | SPEC IOB-CCH-01..08, IOB-ARC-03, DG 3.1 |
| O-2 write cache proven by the time axis; E-cold/E1/E2 read-only | IOB-CCH-07 |
| O-3 target kind `file`; fill re-plan below 0.5 x `R_est`; `filesystem_layer` | IOB-TGT-06..10, IOB-CAL-02, CONSUMERS 8 |
| O-4 (IOB-D-015) worst/cache-free basis both directions; `max` from the best read rung; `read_cache_headroom` | IOB-DER-05/06/08/10 |
| R2-1 a flat line is an upper bound, never a cache-free proof; `rate_cap` only from the cold probe itself | IOB-CCH-05, IOB-CAL-06, fixtures #5, #12 |
| R2-2 file target `--size` required and bounding; root fs 10 % rule; free space re-checked during the fill | IOB-TGT-06, IOB-TGT-10 |
| R2-3 `fresh_fs_alloc`, `mount_discard`, `fresh_semantics_filesystem`; basis only on a `discard` mount; never `fstrim` | IOB-TGT-08, IOB-DER-09 |
| R2-4 `ratio_r` from the best read rung; unproven read value `min(measured_largest, R_cold)` | IOB-DER-06/08/10, IOB-CCH-06 |
| O-5 `fresh_write_bps` check and the rearm cell [I] | IOB-PLA-18, IOB-CAL-10 |
| O-6 full histograms, h = mass below the valley, thresholds [I], adjacency | IOB-CCH-02/03 |
| O-7 what r1002 does and does not prove | SPEC section 20 |
| O-8 NITs | calibration example, README |

## 12. Rejected and open

Rejected: memory balloon (guest-only), fsync/O_DSYNC as cache-proofing, a `rotational` heuristic, hypervisor detection to relax checks, a fixed 16 GiB file, a single global `plausible` bit,
a VM lane (r1002 is the live acceptance). Open (SPEC section 20): installer filesystem lifecycle (OI-1), ceiling history population (OI-2), the `[I]` 10 ms request-time constant (OI-3). A provider
token bucket longer than the observation cannot be excluded by a one-off benchmark; only the ceiling history can show it, and every result states the observation length.

## Decisions added in IOB-03 P3 (coordinator rulings)

* **R-11 `no_cache_observed` (corrected ruling).** A hit-or-miss label for the cold probe is relative to `hit_ref_us` and only means something when a cache exists. On a disk with no cache, or with a flat
  provider cap (the gstammtisch class), the cold latency equals `hit_ref`, so the probe reads "hit-like" and `rate_cap` could never be proven. Two fixes: (1) a **flat cold probe is `rate_cap`, `proven`, whatever its
  latency class**, because a provider cap bounds the sustained rate whether or not reads are cached; (2) a new descriptive state `no_cache_observed` (cold region as fast as the warm one, `R_cold >= 0.9 x cached_ref`,
  unimodal, no cliff, device target) with status **`unproven: cache_not_excluded`** and value `min(measured_largest, R_cold)`. No rate or latency difference was observed between data written first and data written
  last; this is equally consistent with no cache and with a cache at least as large as the written extent, and a larger fill or the ceiling history can separate them. `cold_hit_like` (unproven) stays for a cliff,
  a file target, or a cold rate below the warm reference. **First version of this ruling was wrong:** it made `no_cache_observed` `proven`. Two real-world conditions (no cache; a cache at least as large as the
  fill) collapse into one observation, so certifying it would have let cached data prove a cap (the estate rule "a check is only as strong as what it compares"). The fixtures `cache_larger_than_fill` and the
  no-cache, not-flat disk therefore give the same state and status, and the test asserts that they are indistinguishable.
* **CCH-08 #1 is analytic.** The content simulator did not produce an E1 "good but wrong" ladder (the E1 criteria are strict and its ladder reads `[0, ws)`, which a two-tier model makes rise), so the ladder is generated
  from the header parameters and run through the production estimators. The simulator-based fixture that demonstrates the neighbouring hazard is #2 `fill_history_inversion`: the ladder rises with the working set,
  `cached_ref` equals the miss rate, no cliff appears, and the cap still comes from the cold probe.
* **CCH-08 #2 range.** `R = min(R_cold, R_E2)` with `R_E2 = steady(QD32) x (1 - h2)` is conservative on the mixed largest rung (about 7.3k against a cold 9.8k); the fixture asserts 0.7 to 1.06 x 9.8k.
* **SHP-04 wins over PLA-11.** A poor fit makes the model keys `absent: stage_failed` and the QoS `max` `unproven: shape_fit_poor`; the PLA-11 text now says the same.
* **CAL-01.** The rule wins over the literal numbers: the two densification rungs are the geometric thirds (282.8 and 312.4 GiB). They add a fixed two rungs, so the section 15 allotment sum (3340 s standard, 4180 s thorough) is unchanged and `test_slo_05_*` pins it.
* **REL-01.** Wheel and sha256 only.


## Decisions added in the IOB-03 P3 review wave (2026-10-21)

Two independent reviews (A: safety and the real host; B: measurement, estimators, derivations, proof model) found 11 blocking defects. Each is fixed with a test that goes red without the fix; the reasoning:

* **A single owner for the restore journal.** `restore()` used to end with `remove(journal)` whether or not the session had written it, so a refused or lock-contended invocation deleted the live run's journal. Only the lock
  holder writes, restores or removes the journal (`Safety` refuses to journal without the lock and tracks `journal_written`); `recover_previous` demands the lock.
* **The kernel couples the dirty knobs.** Writing `dirty_bytes` zeroes `dirty_ratio` (and likewise the background pair), and the restore write re-fires the handler. Restoring only the bytes knob left a production host with ratio 0
  while the read-back "verified" the knob that was not the problem. The ratio is journalled before the first change; the originally non-zero member of each pair is written last; both are verified.
* **A container cannot prove the host's mount namespace.** `/proc/1` inside a container is the container's own init, so comparing `/proc/self/mountinfo` with `/proc/1/mountinfo` always agrees. The fact is derived from the PID
  namespace link (the initial namespace has a fixed inode) and the mount namespace link; anything unreadable is a refusal ("run on the host, not in a container"). The same reasoning applies to `/run` (private per container: a lock
  and a journal there exclude nobody).
* **A silent probe is not an empty answer.** `blkid`, `wipefs`, `docker ps` and `systemctl` return codes are checked (`blkid` exits 2 for "nothing found", which is a normal answer; anything else is `probe_failed`), and unit
  names are checked with `systemctl show` because `is-active` reports 3 for both "inactive" and a typo. This is the estate rule "absence for emptiness" applied to subprocesses.
* **Bursts are integrated over the raw series.** A rolling median of 5 removes a burst of one or two samples, so the smoothed integral under-counted the gstammtisch burst (2.98e9 instead of 3.97e9) and a one-sample burst was
  `flat`. The proof's 2x rule now compares against the raw burst bytes (500 s and 540 s are unproven, 560 s proven). Noise cancels in the signed sum; a raw sample above the band within the first two moves the knee.
* **The cap and its proof are one series (B-B3).** The 30 s QD32 fresh/rewrite runs can sit wholly inside a 60 s burst that the 240 s observation outlives, so a `proven` write cap could be twice the sustained rate. Of the two
  remedies the review offered (cap from the observation series, or prove at QD32) the change is the smaller one: the observation runs at QD32, the depth of the cap, on the same extent classes (rewrite extent, fresh extent), and
  the cap is additionally bounded by the observation's own steady rate. The recorded inputs make `value == min(inputs)` checkable.
* **Every input of `proven` must be present.** A missing fresh observation, an unmeasured fresh penalty (`fresh_not_measured`, or `budget_limited` for a budget skip) and a moved WRITE reference of the fresh-vs-rewrite pair
  now make the write cap unproven. The movement flag is per direction because a read reference follows the read cache: a rewrite that evicts the read cache moves the read reference, not the write ceiling.
* **A drifting cold probe is not a rate.** `ColdProbe` carries the series state; only `steady`/`flat` can vouch for `rate_cap` or `cold_measured`, `rate_limited` is judged on the final 60 s window (the one that defines
  `steady`), and a flat cold probe now also respects the E2 cross-check (STA-03). The residual that a limiter on cache-resident data looks like a provider cap remains (R-11 ruling) and is the reason E2 disagreement blocks it.
* **`check` cannot certify what it did not read.** An absent or empty section was "nothing wrong" (exit 0); now a section the document does not carry is a usage error, an emptied one is `absent`, an unknown status is a
  damaged file (exit 5) and a newer schema minor is refused. `section_summary` whitelists the three statuses.
* **S6 read keys are a cached rate.** The S6 cells run on the cache-basis working set, so the read model keys derived from the fit carry the same cache residue as the largest rung: they are scaled by `cache_ratio` exactly as the
  BPS basis is, and unproven otherwise. Unsteady cells leave the fit.
* **Plausibility checks run.** PLA-07, -09, -17 and -18 existed as unit-tested functions that no run called; the calibration now evaluates them (per direction, from the run's own cells), and without the closing S6 reference
  they are `not_applicable` instead of asserting "unmoved".
* **A fresh reference before S6.** The "before" reference of the shape group was taken at the end of rearm, before 480 s of heavy writes; a draining bucket then made a healthy disk look like a moved ceiling and every BPS cap
  `unproven: ceiling_moved`. Six references now cost 180 s, and the fresh observation is 150 s (standard 3340 s, thorough 4180 s).
* **Smaller rulings.** The coupled `IO*`/`BlockIO*` argv forms are all refused; a file target is identified by its resolved path and filesystem (a bind mount of `/` is `/`); a re-init needs a sidecar that matches the file; `:` in
  a path is refused (fio splits it); an active holder is refused outright; an `fio` left by a dead run refuses the next run; a failed second spawn kills the first; the dry run no longer creates an O_DIRECT probe file; a foreign
  `ceilings.jsonl` is rotated aside before the run instead of refusing after the result is written; a dry run only plans with the rate of the same target and a current calibration.

Not decided here (see IMPLEMENTATION-NOTES `DECISION-NEEDED`): a `--dry-run` that runs the live-host preflight, a sequential-cell time proof for BPS writes, and a ceiling-history writer for `seq1m`.

## Decisions added by the coordinator rulings after the first review wave

* **A dry run is a plan, not an acceptance.** `--dry-run` stays plan-only for this release, and its output (human and `--json`) carries `preflight: not_run` so that a clean dry run cannot read as "this target will be accepted". A report mode is backlog IOB-BL-01.
* **Raw-device mutation is refused until a claimed writer exists (IOB-BL-28).** `write_marker` uses kernel `O_EXCL` only while writing the marker, then closes it. That does not reserve the device for later stages. fio 3.39 has no raw-device O_EXCL option; its [HOWTO](https://github.com/axboe/fio/blob/fio-3.39/HOWTO.rst) and [Debian 3.39-1 manual](https://sources.debian.org/src/fio/3.39-1/fio.1/) document `allow_mounted_write` but not an exclusive device open, and upstream [issue 1820](https://github.com/axboe/fio/issues/1820) requests that feature. `lockfile=exclusive` is a fio coordination lock, not a kernel block-device claim. Because fio and `blkdiscard` cannot use the same held descriptor that owns an O_EXCL claim, RealHost refuses raw `write_range` and `discard_range` before launching a mutating process. It probes the device at that boundary with `O_RDONLY|O_EXCL`; on Linux this detects a kernel block-device holder conflict, such as a mounted filesystem or another exclusive holder, and reports `device_in_use`. An ordinary non-exclusive opener does not make the probe fail. If the probe succeeds, RealHost still refuses with `raw_device_write_unsupported`. This keeps raw mutators from launching; it does not establish ordinary-opener exclusion. `--allow_mounted_write=0` remains on device fio jobs as an additional guard against mounted-target writes. The fio floor remains >= 3.30 for its separate latency-field requirement; S0 checks the exact mounted-write option in the installed binary. The first host acceptance must verify that the probe reports EBUSY for a mounted device or another exclusive holder, does not report EBUSY for an ordinary non-exclusive reader alone, and never launches a raw mutator; raw mutation itself remains unavailable.
* **Runtime bindings.** `RealHost.run`/`spawn` enforce the argv allowlist (shape per tool; fio argv must carry only the option keys `FioJob.argv()` builds, each once (abbreviations and repeats refused), `--ioengine=libaio` only; `systemctl stop` only for `iobench-probe-*` units), `write_range` parses the argv and requires exactly the authorised target, offset and size outside the reserved tail, and `--allow_mounted_write=0` on a device target, and `discard_range` requires the exact output of the typed builder. FakeHost enforces the same functions (`devio.assert_writer_bound`, `assert_discard_bound`), so the parity the notes claim is real.
* **The probe unit is waited for.** `systemd-run --wait` returns when the unit has finished (the probe script writes its output only at its end); the unit's return code is checked. Without it the sync-latency cell read the file before it existed.
* **A second signal is not deferred.** A blocking command (`blkdiscard`, `fallocate`) runs in 0.5 s slices; a second SIGTERM/SIGINT/SIGHUP kills it and aborts. The first signal remains a flag honoured at the next tick.
* **A cap's proof comes from the same kind of series as the cap.** The write BPS cap is `unproven: short_observation` until the sequential 1 MiB series is itself time-proven (backlog IOB-BL-02); the IOPS write cap was already tied to its QD32 observation.
* **The fresh observation outlasts the rule.** The proof needs >= 120 s after the knee; fio stamps the last sample of a 120 s run at 119.9x s, so a run exactly as long as the rule could never prove the fresh half. The rule gets no tolerance; the observation is 150 s (standard 3340 s, thorough 4180 s).
* **`check` reads the schema, not the file.** The sections and leaves a result or calibration must carry come from the schema; a missing or emptied one is `absent: missing_from_document` (exit 7), distinct from a stage that was skipped. A superseded calibration (`calibration_superseded`) is distinguished from a missing one.
* **A rung that fails Little's law is dropped upstream** of the ladder, cliff, `cached_ref`, best read rung, E1 and QD90, and listed in `ladder.excluded_rungs`.
* **Known blind spot (movement is judged per direction).** A comparison group is judged by the reference of its own direction, because a read reference follows the read cache content (a rewrite that evicts the cache moves it) and treating that as a ceiling move flagged every healthy run. The cost: a provider throttle that shows only on reads (a shared budget the 15 s write reference absorbs) is not seen by a write cap. The cap is then a post-throttle figure (conservative) but the mixed regime is silent. Backlog IOB-BL-04 and an IOB-ACC-01 plausibility check; the rule was not redesigned.
* **Two-tier caches are tested dynamically.** `test_rev_c_s15_*` runs a 40 GiB fast tier inside a 300 GiB slower tier over a 9.8k backing disk through the Calibrator: E-cold reads the evicted region and reports the backing rate, E2 disagrees with it on the mixed largest rung (`cache_model_undetermined`, unproven), and the write proof counts the bytes after the knee.
* **SPEC section 18 follows the code** where they differed (`cap_basis.value`, `reference`, `shape.fit` keys, E2 timestamps, touch map keyed by stage); every divergence is listed in IMPLEMENTATION-NOTES.

## IBX ladder error study

The one-off IBX study measures how the `veryquick`, `quick`, and `standard`
ladder rungs vary over time and how ext4-file and btrfs-file measurements bias
against the raw reference. The implementation lives in
[`studies/ibx/`](../studies/ibx/README.md), separate from the `iobench`
calibration CLI. The CLI keeps refusing raw writes because fio and
`blkdiscard` cannot hold the same kernel `O_EXCL` descriptor; the study needs a
controlled raw-partition reference and therefore invokes its own bounded fio
job files through the approved host wrapper. It records no SSH or host
orchestration code.

The driver fails closed on target geometry, active io.cost and cgroup IO
controls, monitor thresholds, write/time budgets, and timer state during live
runs. It does not change IO controls. The IO snapshot covers the benchmark
cgroup and each ancestor for `io.max`, `io.weight`, and `io.bfq.weight`. A control file missing
from that chain means the kernel/cgroup has no setting to enforce; its absence
is recorded alongside present values so the before/after check detects a
control appearing or changing. An existing but unreadable or malformed file
refuses. `io.max` entries are inactive only when every per-device value is
`max`; `io.weight` and `io.bfq.weight` must report default 100 with no active
device override. Because `io.latency` can throttle peers, the snapshot also enumerates
sibling cgroups at each level of the benchmark path and refuses active latency
targets there; those peer controls are checked on every snapshot call. Peer
`io.weight` is not judged. The target disk scheduler is read from
`/sys/block/<disk>/queue/scheduler`, stored in the manifest and compared in
the IO snapshot. Peer `io.bfq.weight` is judged when the selected target
scheduler is `bfq`, because sibling group weights then affect proportional
bandwidth. With any known non-BFQ scheduler, peer BFQ weights are inert,
including `none` and `mq-deadline`. A
missing, unreadable, or malformed scheduler is recorded as unknown and peer
BFQ weights are checked conservatively; active values refuse with a message
that names the cgroup control. Peer membership is not part of before/after
equality because systemd services and containers can come and go during a
study. Missing optional files mean no setting. Root `io.cost.qos`
must be disabled, and the built-in
`ctrl=auto model=linear` model is accepted while user/custom models refuse.
Other siblings' `io.max` caps are not inherited by the benchmark cgroup, so
they do not prevent this study from running. A raw run requires an operator acknowledgement
containing the exact partition path, byte size, and PARTUUID; resume compares
those facts with state and refuses a replacement. Sysfs partition,
exclusive-claim, holder, mount, and swap checks run before any discard. The
running-system-disk refusal remains the default. **Study decision D-IBX-002**
allows a controller-policy exception only when the operator supplies both
`--allow-system-disk-partition` and an acknowledgement derived at run time from
hostname, target disk, root filesystem device(s), and target PARTUUID. The
explicit flag is consent; the per-run acknowledgement is a drift check that
binds that consent to current host and target facts. This relaxes only the
disk-sharing refusal: the target partition itself must still be unmounted and
not swap-active, and every identity, holder, geometry, exclusive-claim, and
IO-control guard remains. Run state, cell records, plan, and analysis preserve
the acknowledgement and observed system mounts and sibling swap devices; the
stable identity is checked on resume and volatile mount/swap facts are stored
per attempt. The result is marked as including system and sibling-swap
activity. This controller policy was requested on 2026-11-13
because approved disposable compute hosts have one virtual disk. Btrfs file
targets resolve the filesystem UUID through
`/sys/fs/btrfs/<uuid>/devices`, because the mounted filesystem's `MAJ:MIN` may
be anonymous and unrelated to its backing member's number.

The `run --dry-run` path exercises target identity, schedule, IO-control,
memory PSI, `MemAvailable`, free-space checks, a successful initial
host-liveness probe, and fio rendering without creating output or changing the
target. It does not require fstrim, apt-daily, or governance timers to be
stopped. Live runs require those timers inactive. I/O PSI remains recorded as
advisory because the saturating fio workload can cause high I/O pressure by
itself. Host-protection stops use memory PSI `full avg10`, `MemAvailable`, and
free-space floors plus a separate bounded `system.slice` liveness service that
fsyncs a temporary file on the root filesystem, checks the local SSH banner,
and verifies the configured cockpit Docker container with `docker exec`; it
does not require a systemd unit for the runner. The SSH host and port default
to `127.0.0.1:22`, and the container name defaults to `mdt-cockpit-runner`;
all three can be configured with their `--liveness-*` run options. The first
successful probe is required before target writes; subsequent probes run at a
recorded 10-second default interval that can be set with
`--probe-interval-seconds`. A configured number of consecutive slow or failed
liveness samples stops the run; wall-time and byte caps remain hard stops.
When `--fsync-cell` is selected, dry-run also checks for one Docker cgroup
identity, the loaded background slice, the wrapper, and the configured
collector image. An SSH root session without the Docker cgroup scope refuses
before study work begins.

The writer meter has a 500 GB decimal cap (500,000,000,000 bytes). The actual
fio job caps for the full base schedule total 495.122866176 GB, including the
conditional t+6h standard rung, leaving 4.877133824 GB. Caps are rounded down
to whole blocks; the nominal 15/26/100 GB rung budgets have effective writer
sums of 14.999912448/25.999986688/99.999891456 GB. A part (c) repeat costs
49.123688448 GB and either raw quick/veryquick repeat costs 40.999899136 GB,
so all three adaptive repeat sets report `skipped` under this cap. No base rung
is reduced. An optional file-standard set has a nominal 100 GB budget and an
effective 99.999891456 GB cap; it runs only if its remaining cap
and the unpaid caps of required cell-sets still ahead fit; otherwise it reports
`skipped` before fio starts. The t+6h standard
rung is conditional on the 6.2 boundary rule or the need for a second standard
point to calculate `V`. File-standard selection uses **study decision D-IBX-001**:
`T/1.5 ≤ e ≤ T+C`; rev 4.1 does not specify that numeric file cutoff. Its
implementation and analysis label that choice explicitly.

The part (c) repeat runs only after a complete first assessment confirms the
degradation criteria. A complete assessment that does not confirm them returns
`skipped` so the scheduled runbook continues; incomplete evidence remains an
error.

The result table applies design 6.3's reference/error computation and 6.4's
rung verdicts; quick/veryquick latency-under-load values use the documented
QD32 p99 proxy, and the noise-band action is reported with each measured
number. Every read job stays within fio's recorded written extent. The
study-day schedule, timer restoration, fsync collection, and exact invocations
are in the [consumer procedure](CONSUMERS.md#ibx-host-study).

## Decisions added by the pre-release review (2026-10-27)

* **The Host binds, not only the Runner.** `Runner.spawn` routed target writers through `write_range`, but the Host accepted any fio writer, any `blkdiscard` and any `systemd-run` command carrying `RuntimeMaxSec=` through a plain `run`/`spawn`; one misrouted call in the core would have been enough. `check_argv` now carries the rule itself (RealHost and FakeHost through the same function): a discard only through `discard_range`, a fio writer only to an `iobench-*` load file unless bound, `--rw` only the four planned workloads (`trim` is a discard in fio's vocabulary), and the probe unit's exact argv. The allowlist is a second, caller-independent layer; the Runner's extent checks stay.
* **The work dir holds one cell.** IOB-TGT-05 sizes the work dir for one cell's volume and IOB-DRN-01 says the load writer is "removed behind itself"; the matrix kept every cell's files until the restore. A cell's files are removed as soon as the cell is reduced; the journal still lists them for a crash.
* **A guard that cannot read is an abort.** `_validate_guards` already refuses a guard that could never fire at S0; the same reasoning applies mid-run: if the protected cgroup's `io.pressure` vanishes, the run aborts (exit 8, restored) rather than continuing unprotected or ending in a traceback.
* **Division of labour with iogovmon** (`iogovmon/`, recommend-only recorder): iobench characterises offline with fio on a dedicated target in a confirmed window; iogovmon observes the live host long-term with tiny unprivileged probes and recognises shifts. iobench stops owning the ceiling-history population (D-011/OI-2: iogovmon's lanes feed `ceilings add --source probe` through a bridge still to be written) and the governor histogram format (D-IOC-3) is decided once for both. The earlier "IOGOV adaptive-governor design" item is superseded by iogovmon v1. Live facts carried into P4: stalls on the live host are reclaim-driven (BACKLOG IOB-BL-12: the io.pressure guard does not see that class), `io.latency` is not compiled in and `io.prio.class` is unavailable (neither is used), iocost is enabled with `mq-deadline` and `nr_requests` 16 (inside R2's range).
