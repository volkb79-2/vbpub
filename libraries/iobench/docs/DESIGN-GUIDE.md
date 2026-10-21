# iobench DESIGN-GUIDE (why it works this way)

This is the WHY document. [README](../README.md) says WHAT it does, [CONSUMERS](CONSUMERS.md) says HOW to adopt it, [SPEC](SPEC.md) is the normative
requirement list. The design record moved here from `vbpub-release-program-20261019/IOB-02-DESIGN.md`, which is now a pointer to this file.

Evidence labels: **[M date host]** measured, **[I]** inferred. Sources: `IO-BENCH-01-report.md` (r1002, 2026-10-20), `IO-STALL-TESTING-GUIDE.md` and
`scripts/gstammtisch-guide/HOST-IO-INTERFERENCE.md` (gstammtisch), `IOCOST-WIZARD-DESIGN.md` (decisions D-IOC-1..4), `review-IO-BENCH-01.md`
(findings B-01, B-03, P-01 that the prototype must not repeat).

## 1. The problem: a benchmark that measures a cache

The stock io.cost generator (`iocost_coef_gen.py`, the kernel's `tools/cgroup/iocost_coef_gen.py`) runs short max-queue-depth fio jobs (sequential
128 MiB-class and random 4 KiB, read and write) on a 16 GiB file and prints **one point estimate of six numbers**. It has no latency, no variance, no
time axis and no cache-proofing.

* On a VM disk it reads the hypervisor and provider caches: r1002 gave 4.296 GB/s read, 4.220 GB/s write and ~100k random 4 KiB IOPS in both
  directions [M 2026-10-20 r1002]; gstammtisch gave 2.0 GB/s write and 94k read IOPS from a disk that saturated at ~14-20 MB/s [M].
* `O_DIRECT` bypasses only the **guest** page cache. fsync and O_DSYNC are acknowledged by a persistent write cache and prove nothing. A memory balloon
  removes only the guest page cache (buffered 2 GiB re-reads fell from 63k to 16k IOPS; direct IO did not change) [M r1002].
* The provider's ceiling **moves**: 18, 16, 14 and 6 MiB/s on one day on gstammtisch, flat in bytes/s across IO sizes [M 2026-10-20 gstammtisch,
  guide s14]. One short measurement is wrong by up to 3x.
* io.cost's `vrate` adapts the **scale** (one multiplier on every price) but not the **shape** (per-IO versus per-byte cost, read versus write, seq versus
  random). A wrong shape makes vrate settle on today's IO mix and misprice tomorrow's (guide s14).

iobench keeps the six generator output keys (`rbps rseqiops rrandiops wbps wseqiops wrandiops`) so every consumer keeps working, and fills them from
cache-proof, time-resolved data.

## 2. Calibration versus matrix (controller remark, 2026-10-21)

The slow part (finding where the caches end) does not change between two experiments on the same disk; the interference matrix is what an operator
iterates. They are therefore two phases with two artefacts:

| | Calibration | Matrix iteration |
|---|---|---|
| Frequency | once per host or storage class; again on an **identity change** or a **sustained ceiling shift** (REQ `IOB-MAT-04`) | as often as wanted |
| Time | 30-75 min (profile `standard` 30, `thorough` 75, `quick` 12) | 6 cells ~ 10-15 min; one knob A/B ~ 3-4 min with a repeat |
| What | ladder to the cliff, fresh vs rewrite, cache-model fit, ceiling, S6 cost-shape grid, S7 latency curve | short 60-90 s cells with a quiet gap of at most 60 s, on the already-calibrated working set |
| Artefact | `calibration.json` | `result.json` (references the calibration by id) |

A matrix run refuses to start without a **current** calibration (identity match) and refuses to *derive caps or the io.cost model* from anything but it.
The calibrated working set is fixed (extent map recorded), so every cell writes and reads only blocks the calibration established as written.

## 3. Cache-proofing: what defeats what

| Technique | What it defeats | What it does not |
|---|---|---|
| `--direct=1` | guest page cache | any host/provider cache |
| Working set past the **detected cliff**, with a cache-model fit | host/provider read and write-back cache | a cache as large as the device (reported as `cache_residual_possible`) |
| Steady tail = median of the last 25 % of a 2-5 s series, knee/burst detection | token buckets and warm-up | a bucket larger than the run (the ceiling history covers it, section 5) |
| Read only data **written by this calibration** | zero-page reads of unmapped space (a discarded range read at 99.9k IOPS [M r1002]) | - |
| **Fresh versus rewrite** series | provider allocation / copy-on-write cost (random writes to fresh space ran at 3.6k IOPS, 7 ms p50, against 71k rewritten [M r1002]) | - |
| **Flat-line detection** (CV < 1 % over >= 60 s => `rate_limited`) | reading a provider cap as a capability (r1002 sequential read is exactly 4096 MiB/s [M]) | - |

### 3.1 The cache model (new; found while preparing this design)

The IO-BENCH-01 ladder on r1002 did not plateau: random read at 300 / 350 / 400 GiB gave 46k / 31k / 24k IOPS (whole device 406 GiB) [M]. A cache of size
C in front of a rate-limited backing store gives `iops(ws) = R / (1 - min(1, C/ws))` for ws > C. Fitting the three points gives C ~ 244 GiB and
**R ~ 9.4k IOPS** [I, three points, one free parameter overdetermined]; the 400 GiB "steady" figure of 24k was therefore still ~2.5x a cache-free
rate. The calibration therefore fits `C` and `R` from the post-cliff rungs and reports both `measured_largest` and `uncached_est`; **caps derive from the
lower of the two** when the fit is good, and carry `cache_residual_possible` when it is not. This is a hypothesis the standard profile tests (P5 of IOB-03):
if the same device shows a flat floor at 100 % working set on a different, bigger target, the model is wrong and the flag is the safe state.

## 4. Stages and why each exists

Every stage exists because a derivation reads its output; a stage nothing reads is not run.

| Stage | Measures | Feeds |
|---|---|---|
| S0 identity and preflight | disk chain under `/`, model/serial/size/partuuid, filesystem, scheduler, `max_hw_sectors_kb`, RAM, PSI, running containers, every slice's `io.max` (read only) | validity, refusals, sizing |
| S1 rate probe | 20 s buffered sequential write: `R_est` | time-bounded sizing, slow-disk detection |
| S2 ladder | direct random 4 KiB read/write QD1/8/32 over a doubling working set (1 GiB .. target), steady tail per rung, `QD90`, cliff, cache model | caps, io.cost model, `nr_requests`, plausibility |
| S3 drain rate | `R_drain`: device bytes/s under the reference writeback load on the target fs | `dirty_bytes`, caps |
| S4 sweep | sync-latency probe under buffered load across candidate settings; 6-8 cells, REF repeated for the noise floor | final choice of `nr_requests`, `max_sectors_kb`, confirmation of `dirty_bytes` |
| S5 read cost | direct sequential reads at each candidate `max_sectors_kb` | the read-floor rule for `max_sectors_kb` |
| S6 cost shape | direct IO over sizes {4K,16K,64K,256K,1M} x {seq,rand} x {read,write} x {rewrite,fresh}: a linear fit `t_io = c_io + bytes*c_byte` per direction and pattern with residuals; classifies the metering `byte`/`iops`/`mixed`/`fresh-penalty` | the six model keys from `c_io` and `c_byte`; the byte-proportional IOPS rule |
| S7 latency-vs-load | p50/p90/p95/p99 per direction at 10/30/60/90 % of the ceiling | QoS `rlat`/`wlat`, the percentile choice |
| S8 ceiling history | not a run stage: append-only plateau observations from the benchmark, a governor or a timer probe | the vrate bounds `min`/`max` |

The S4 method is the gstammtisch A/B (`ab_runner.sh`) shrunk: the probe appends 200 B and `fdatasync`s every 0.5 s on the same filesystem while a
buffered writer (new 1 GiB files) and a 4 KiB random-write job with `fsync=16` run. Measured results that fix its design [M 2026-10-20 gstammtisch]:
bounding bytes in flight removed multi-second stalls without losing throughput (A4: probe p99 0.54 s against 34.7 s); identical cells differ ~2x
in 60 s (so a neighbour must beat REF by more than the measured noise ratio in a cell *and* its repeat); 1 MB requests were the worst cell and 64 KB the
best, at a sequential-read cost of ~80 % (1.2-1.4 GB/s to 0.25-0.3 GB/s).

## 5. Derivation rules (revised by D-IOC-1..4)

Targets are explicit and operator-visible; defaults are *policy* defaults, correct in the absence of information, never a substitute for a measured fact.

| Target | Default | Meaning |
|---|---|---|
| `W` | 2 s | worst-case sync wait behind ordered data and queue |
| `P` | 250 ms | S4 acceptance threshold for the probe's `fdatasync` p99 |
| `F` | 25 % | smallest acceptable sequential read throughput relative to `max_hw_sectors_kb` |
| `U` | 70 % | dev aggregate share of the steady ceiling (see conflict C-1) |

* **R1 `dirty_bytes`** = largest power-of-two MiB <= `W * R_drain - inflight`, `inflight = nr_requests * max_sectors_kb * 1024`, clamped to
  `[4 MiB, 25 % of the kernel's effective threshold]`; `kernel` when the unclamped value reaches the effective threshold. **R1b** `dirty_background_bytes` = dirty / 4 (`[I]`: the matrix did not separate it).
* **R2 `nr_requests`** = `2 * QD90` rounded to a power of two in `[8, 64]` (r1002 saturated at QD8; gstammtisch q4 gave nothing over q8-16); `kernel` if not smaller than the current value.
* **R3 `max_sectors_kb`** = largest power-of-two KB <= `R_drain * 10 ms` (`[I]` mechanism), clamped to `[32, max_hw]`; raised to the smallest candidate with `read_ratio >= F` unless S4 shows that candidate's probe p99 > `P` (then `read_floor_violated`).
* **R4** the chosen triple must show probe p99 <= `P` in the REF cell and its repeat, else `target_missed` is recorded and shown, never loosened silently.
* **R5 dev caps** (supersedes IOB-02 R5): one **dev aggregate** safety cap, `U * steady ceiling` per key from the cap-basis figures; **no per-tier caps** are proposed (operator direction, guide s13: fairness comes from io.cost weights, one aggregate cap). The tier sub-ceiling stays host-setup's `DEV_SUBSLICE_IOPS_PCT`.
* **R6 io.cost model and QoS** (supersedes IOB-02 R6 per D-IOC-1/2/3):
  * model basis = the **worst observed plateau** per direction (ceiling history, section 6, plus this run's per-window plateaus); `wbps = worst`; byte-metered => `wseqiops = wrandiops = wbps / 4096` (and the read analogue), otherwise the S6-fitted `1/c_io`;
  * `min = 100` (model = worst), `max = ceil(110 * best / worst)`; the swap guard is thereby satisfied by construction;
  * `wpct = 90`, `wlat = 2 * (S7 light-load p90)` rounded up to 5 ms and bounded to [10, 50] ms; `rpct = 95`, `rlat = 2 * light-load p95` bounded to [2, 20] ms. Source of the histogram: the governor if one runs, else this benchmark's S7 (D-IOC-3).

**Worked check against the evidence** (the first replay oracle): gstammtisch with `R_drain` ~10.5 MB/s gives dirty 16 MiB / 4 MiB, `nr_requests` 16, `max_sectors_kb` 64 (the measured best), and r1002 gives `kernel` for dirty and sectors.

## 6. Ceiling history (S8)

`ceilings.jsonl` is history, not a run result: one line per observation `{ts, dir, plateau_bps, duration_s, source: benchmark|governor|probe}`, read as
worst / median / best over a window. It is identity-bound but never invalidated by age. A **sustained ceiling shift** (the median of the last 12 h
differing from the calibration's by more than 25 % for two consecutive windows) sets `recalibrate_recommended` in `iobench check` (it never invalidates).
Before any history exists the worst/best come from the benchmark's own windows and the result says `history: "single-run"`.

## 7. Code location and migration (from IOB-02, accepted)

New library `libraries/iobench`: stdlib plus `fio`, a pure core behind an injected `Host`, a thin `iobench.cli` (the shell adopts `cli-extended`, the core never imports it).
In-repo consumers use it from the checkout (install by file copy, the precedent of `install.sh` installing the generator; and "Consuming assay from inside
vbpub"); out-of-repo consumers get a cmru-released wheel and zipapp with sha256 (never PyPI). New code, no GPL code.

| Phase | Change |
|---|---|
| M0 (IOB-03) | library ships, schema 1, replay tests; no consumer changes |
| M1 | `mdt-io-baseline.py` becomes a wrapper; `/var/lib/mdt/io-baseline.env` becomes a projection; `--with-baseline` calls iobench |
| M2 | debian-install-v2 `_run_io_benchmark` calls iobench against its throwaway partition |
| M3 | `iocost_plausibility.py`'s `rotational` rule retired |
| M4 | `iocost_coef_gen.py`, its patches and `.sha256` flow deleted |

## 8. Safety design

* **Target guard (finding P-01)**: a target is usable only if it has *iobench's own marker* (written by `iobench target init`, which itself refuses a mounted, swap, root-disk, or signature-bearing device unless `--i-own-this-device` is given together with a matching `--expect-size` and `--expect-partuuid` (or `--expect-serial` when the device has one; r1002's virtio disk has none)). The marker records size and partuuid; every run re-verifies them.
* **Never `io.max`**: no `io.max`, `IOReadBandwidthMax=`, `set-property`, `daemon-reload`. Slice `io.max` is read before and after and a difference is an error (the systemd per-spelling hazard, guide s6).
* **Restore on every exit** from a journal in `/run/iobench/restore.json` written before the first change; verified by read-back.
* **Volumes from time and measured rate**, never fixed (the gstammtisch 2.6 GB copy saturated the disk for 10 minutes at 14 MB/s). On a slow capped disk (`R_est` < 5 MB/s, or a fill that does not fit the budget) the calibration works **time-bounded from the measured cap** and labels the result `cache_proof: limited-by-budget` (conflict C-2).
* **Live host**: refuse with production containers active unless `--confirm-quiet-window`; PSI abort rule; the sweep changes host-global `vm.dirty_*`.

## 9. Decisions

IOB-D-001..014 are those of IOB-02 section 9 and are **accepted** (controller, 2026-10-21): D-001 library, D-002 io.cost moves in (phased), D-003 CLI shell adopts
cli-extended, D-004 targets as wizard keys with policy defaults, D-005 raw read-only reads opt-in, D-006 standard first run / quick re-measure, D-007 refuse unless
confirmed, D-008 identity-only validity, D-009 slow disk, D-010 never branch on hypervisor detection, D-011 long-term probe later, D-012 re-implement without GPL code,
D-013 fresh-block rung, D-014 never persists.

**Conflicts with the newer decisions (listed as required):**

| ID | Conflict | Resolution used here |
|---|---|---|
| C-1 | IOB-02 R5 proposed `U = 40 %` and a gates tier of 1/3 of dev; guide s13 (operator direction) says one aggregate safety cap at ~70 % and no per-tier caps | follow s13: one dev aggregate cap, `U` default 70 %, no tier caps proposed |
| C-2 | IOB-02 D-009 said "skip the sweep and return formula-only proposals" at `R_est` < 5 MB/s; the controller remark says work time-bounded from the measured cap | time-bounded run; the sweep is skipped only if the quiet gap cannot drain the dirty pool within its bound (then `formula-only`, labelled) |
| C-3 | IOB-02 R6 (`rlat/wlat` = 3-4x idle QD1 p99, model from steady values) differs from D-IOC-2/3 (model = worst plateau, min = 100; latency from `2 x p90/p95`, bounded) | D-IOC rules |
| C-4 | IOB-02 D-006 default "standard on first run" vs D-IOC-4 (wizard asks, configurable default) | the profile default is a wizard key; `standard` stays the engine default |
| C-5 | D-003 "CLI adopts cli-extended" requires the surface and review artefacts and the cli-extended audit lane | accepted; in scope of P3 as `IOB-CLI-10`; the pure core never imports it |

## 10. Rejected and open

Rejected: memory balloon (guest-only), fsync/O_DSYNC as cache-proofing, a `rotational` heuristic, hypervisor detection to relax checks, keying anything on a
fixed 16 GiB file. Open: a provider token bucket larger than one run (only the ceiling history can show it; none seen on r1002); `none`/`mq-deadline` and io.cost as
the *active* controller were never swept; the cache model is fitted to a single device class. The result states what was and was not confirmed.
