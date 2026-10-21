# iobench DESIGN-GUIDE (why it works this way)

This is the WHY document. [README](../README.md) says WHAT it does, [CONSUMERS](CONSUMERS.md) says HOW to adopt it, [SPEC](SPEC.md) is the normative requirement list.
The design record moved here from `vbpub-release-program-20261019/IOB-02-DESIGN.md` (now a pointer). Revision 2 (2026-10-21) folds in the P1/P2 review and the
controller rulings R-1..R-10 (section 11).

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

### 3.1 Why a size-only cache defence is not enough (the r1002 lesson)

The ladder on r1002 never plateaued: random read at 300 / 350 / 400 GiB gave 46k / 31k / 24k IOPS on a 406 GiB partition [M]. A cache of size C in front of a rate-limited
backing store gives `1/iops = (C/ws)/F + (1 - C/ws)/R` for `ws > C` (F the cached rate, R the miss/backing rate); that is **linear in 1/ws**, so a fit has two parameters.
Three points leave one degree of freedom (the review recomputed R ~ 9.8k, C ~ 265 GiB, residual ~2 %); almost any convex decreasing triple would be labelled "good". With a
cache comparable to the device (C ~ 65 % of it), the hit ratio stays >= 0.6 on every rung that fits the device, so the largest measurable rung is still mostly cache
hits [I]: the 24k "steady" figure was probably ~2.5x a cache-free rate.

Hence two **independent** estimators must agree before a cache-dependent value is `proven`:

* **E1, hit-ratio fit across rungs**: needs >= 4 post-cliff rungs, monotone, `0 < C < smallest post-cliff rung`, `r2 >= 0.97`, every leave-one-out prediction within 15 %. This is why
  the planner densifies the ladder between the cliff and `WS_max` (up to four rungs); on r1002 that is 276-345 GiB.
* **E2, per-IO completion-latency split**: a QD1 clat percentile ladder at the largest rungs. A cache-hit mode and a miss mode (>= 2.5x apart, each tight) give the hit fraction
  `h2` and the miss time; `R_E2 = iops_largest x (1 - h2)`. Overlapping or unimodal distributions give no E2.
* They agree when `abs(h1 - h2) <= 0.15` and `R_E1 / R_E2` lies in [0.7, 1.43]. Anything else is `unproven: cache_model_undetermined` with both estimates kept.

Degenerate cases are first-class outcomes (SPEC IOB-CCH-04/06), each with a controlled-wrong fixture:

* a **true plateau** (flat within 10 % over >= 3 post-cliff rungs, unimodal miss-like latency) is the *good* case: no cache residue, `proven`, never labelled poor;
* **no cliff** is valid: a single miss-like mode (or a flat-line rate cap) is `proven` cache-free; a single hit-like mode is `unproven: no_cliff_hit_like_mode`; anything else is undetermined;
* **non-monotone rungs**, **C >= the smallest post-cliff rung**, **two rungs**, **noise-only triples**: E1 is `poor`/`insufficient`, never an exception and never `good`;
* the **recorded r1002 rungs that fit the device** (<= 345 GiB) contain a single post-cliff point; the honest, asserted outcome is `undetermined`. Whether a densified
  calibration on r1002 reaches `cliff_modeled` is what IOB-03 P4 measures. If the estimators disagree there, the caps stay `unproven` and the report says so.

A cap never exceeds the measured largest-rung rate whatever any fit says, and a cap whose basis may still contain cache hits is never `proven` on a single fit (IOB-STA-03).

### 3.2 Bandwidth caps need their own basis

The ladder is random 4 KiB, so `RBPS`/`WBPS` have no basis in it. The basis is the steady bytes/s of the sequential 1 MiB QD16 cell at the cap-basis working set, scaled by the
same direction's `cache_ratio = R / measured_largest` when that is proven (else 1 with `unproven: cache_residual_possible`); a flat-line sequential cell (r1002 read 4096 MiB/s) is itself
a proven upper bound; with no such cell the BPS caps are `absent: bps_basis_missing`.

### 3.3 Moving ceilings are findings, not failures (ruling R-8)

The provider's rate moves within a day (6-18 MiB/s [M]). Comparing two numbers measured minutes apart (random vs sequential bytes, fresh vs rewrite, the metering grid) can therefore
fire without any cache. Every comparison group is bracketed by a **reference cell** (random 4 KiB QD8 on 1 GiB, 15 s, before and after, pairs interleaved `[ref, A, B, ref]`). If the reference moved > 15 %, the
comparison is `unproven: ceiling_moved`, both values are kept, and the move is appended to the ceiling history as a `reference_shift` observation. It is not a plausibility failure.

## 4. Cache-proofing: what defeats what

| Technique | What it defeats | What it does not |
|---|---|---|
| `--direct=1` | guest page cache | any host/provider cache |
| Working set past the detected cliff, with E1 and E2 | host/provider caches | a cache that E1/E2 cannot separate (then `unproven`) |
| Steady tail = median of the last 25 % of a <= 2 s series, knee/burst detection | warm-up and bursts shorter than the observation | a token bucket longer than the observation: every result records `bucket_exclusion_s<=N`; `standard` adds a 240 s write and 120 s read observation, `thorough` 900/240 s (the 0.9 -> 3.2 GB/s step at ~430 s seen on r1002 needs `thorough`) |
| Read only data **written by this calibration** (argv-confined to the recorded extent) | zero-page reads of unmapped space (99.9k IOPS on a discarded range [M r1002]) | - |
| Fresh vs rewrite series (**writes only**; reads are always of data written earlier) | provider allocation / copy-on-write (random writes to fresh space ran at 3.6k IOPS, 7 ms p50 against 71k rewritten [M r1002]) | - |
| Flat-line detection (CV < 1 % over >= 60 s => `rate_limited`) | reading a provider cap as a capability (sequential read exactly 4096 MiB/s [M r1002]) | - |

## 5. Stages and why each exists

Every stage exists because a derivation reads its output.

| Stage | Measures | Feeds |
|---|---|---|
| S0 | identity (devno, PARTUUID, size, model/serial if any), disk chain, scheduler, `max_hw_sectors_kb`, RAM, PSI, containers, every slice `io.max` (read only) | validity, refusals |
| S1 | 20 s direct sequential write to the target's first GiB: `R_est` | time-bounded sizing, slow-disk detection |
| fill + S2 ladder | sequential fill of `[0, WS_max)`; random 4 KiB read/write QD32 over a doubling ladder + densification; QD1/QD8 at the largest | cliff, `QD90`, E1 inputs |
| CDF runs | QD1 clat percentile ladders at the smallest and largest three rungs | E2 |
| fresh/rewrite, reference cells | 4 KiB random writes to a discarded extent vs a written one; the reference pairs | fresh penalty, `ceiling_moved` |
| observation runs | 240 s write / 120 s read at the largest rung (`thorough`: 900/240 s) | bucket exclusion |
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
* **R6 model and QoS**: model basis = the **worst plateau** per direction (D-IOC-2). Byte-metered: `wbps = worst`, `*seqiops = *randiops = bps/4096`; otherwise from the S6 fit. `min = 100`; `max = ceil(110 x max(best_r/worst_r, best_w/worst_w))` over directions with history; `wpct 90`, `wlat = 2 x light p90` rounded up to 5 ms in [10, 50] ms; `rpct 95`, `rlat = 2 x light p95` rounded up to 1 ms in [2, 20] ms. Histogram source: the governor if running, else S7 (D-IOC-3). Without ceiling history, worst/best come from this run's own windows and every dependent value is `unproven: worst_from_single_run`.

**Worked check against the evidence** (replay oracles): gstammtisch with `R_drain` 10.5 MB/s gives dirty 16 MiB / 4 MiB, `nr_requests` 16, `max_sectors_kb` 64 (the measured best); r1002 gives `kernel` for dirty and sectors.

## 7. Ceiling history (S8)

`ceilings.jsonl` is history, not a run result: `{ts, dir, workload, plateau_bps, duration_s, source, note?}`, worst / median / best per `(dir, workload)` over a window; identity-bound,
never invalidated by age. `seq1m` plateaus feed `*bps`, `rand4k` plateaus feed IOPS keys (a 4 KiB plateau is never read as a bandwidth). `recalibrate_recommended` fires when the median of
the last 12 h differs from the calibration's by > 25 % in two consecutive windows; until a probe or governor populates the file (D-011) the history is `single-run` and the result says so.

## 8. Code location and migration (from IOB-02, accepted)

New library `libraries/iobench`: stdlib plus `fio`, a pure core behind an injected `Host`, a thin `iobench.cli` that adopts `cli-extended` (so the **CLI** needs `cli-extended` on
`PYTHONPATH`; the measurement core does not). In-repo consumers copy the source (precedent: `install.sh` installs the generator; AGENTS "Consuming assay from inside vbpub"); out-of-repo
consumers get a cmru-released wheel and zipapp with sha256 (never PyPI). New code, no GPL code.

| Phase | Change |
|---|---|
| M0 (IOB-03) | library ships, schema 1, replay tests; no consumer changes |
| M1 | `mdt-io-baseline.py` becomes a wrapper; `/var/lib/mdt/io-baseline.env` becomes a projection; `--with-baseline` calls iobench |
| M2 | debian-install-v2 `_run_io_benchmark` calls iobench (open item OI-1: installer needs a raw target plus a separate work-dir filesystem) |
| M3 | `iocost_plausibility.py`'s `rotational` rule retired |
| M4 | `iocost_coef_gen.py`, its patches and `.sha256` flow deleted |

## 9. Safety design

* **Target guard (finding P-01)**: a target is usable only with iobench's own marker (the last 1 MiB), written by `iobench target init`, which refuses a mounted (checked in the caller's **and** PID 1's mount namespace), swap, root-disk or signature-bearing device unless `--i-own-this-device` is given together with the matching `--expect-size` and `--expect-partuuid` (or `--expect-serial`); a device with neither identity is refused. The marker is re-verified before **every** writing stage (fio, discard, fill) in every profile; `--installer` only waives the live-host refusals. iobench never runs `mkfs`, `mount`, `umount` or a bare `wipefs` (no builder exists).
* **Never `io.max`**: no `io.max`, `IOReadBandwidthMax=`, `set-property`, `daemon-reload`. Slice `io.max` is read before and after and a difference is exit 9 (the systemd per-spelling hazard, guide s6).
* **Restore on every exit** from a journal in `/run/iobench/restore.json` written before the first change; a knob is restored only if its value still equals what iobench applied (never clobber a newer setting); created work-dir files are removed; verified by read-back.
* **Volumes from time and measured rate**, never fixed; a truncated stage yields an honest failed stage, not a guessed value. On a slow capped disk the calibration works time-bounded from the measured cap (conflict C-2).
* **Live host**: refuse with production containers or listed production units active unless `--confirm-quiet-window` (unlisted systemd units are not detected, and the refusal text says so); abort needs `--protect-cgroup` and `--abort-psi` (no defaults); the sweep changes host-global `vm.dirty_*`.

## 10. Decisions

IOB-D-001..014 are those of IOB-02 section 9 and are **accepted** (controller, 2026-10-21): D-001 library, D-002 io.cost moves in (phased), D-003 CLI shell adopts cli-extended, D-004 targets as
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
| R-4 no cliff is valid; miss-like unimodal => proven, hit-like => unproven; `quick` reports unproven | IOB-CCH-04, IOB-PRF |
| R-5 "fresh" is writes only; one matrix count | IOB-CAL-07, IOB-SHP-01, IOB-SWP-01 |
| R-6 discard, fill, mkfs through the same marker guard; no installer bypass; tail oracle for every writer | IOB-TGT-03/04, IOB-ARC-05 |
| R-7 noisy-knee fixture fixed (18000/22000 alternation, CV 0.10) | IOB-SER-02 |
| R-8 moving ceiling is a finding; reference cells; `ceiling_moved` | SPEC section 8, DG 3.3 |
| R-9 byte metering needs both tests | IOB-SHP-03, DG 5.1 |
| R-10 no VM lane; assay mutation lane registered | IOB-ACC-01, IOB-GATE-01, `run-gate.toml`, `assay.toml` |

## 12. Rejected and open

Rejected: memory balloon (guest-only), fsync/O_DSYNC as cache-proofing, a `rotational` heuristic, hypervisor detection to relax checks, a fixed 16 GiB file, a single global `plausible` bit,
a VM lane (r1002 is the live acceptance). Open (SPEC section 20): installer filesystem lifecycle (OI-1), ceiling history population (OI-2), the `[I]` 10 ms request-time constant (OI-3). A provider
token bucket longer than the observation cannot be excluded by a one-off benchmark; only the ceiling history can show it, and every result states the observation length.
