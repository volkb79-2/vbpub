# iobench SPEC

Normative. Words MUST / SHOULD / MAY are used in the RFC 2119 sense. Each requirement has an id `IOB-<AREA>-<nn>` and an **Oracle**: the test (or
mechanical check) that goes red when the requirement is violated. Oracles are named `tests/test_<area>.py::test_<id>_<slug>`; the docs-sync test
(`IOB-DOC-01`) checks that every id in this file has at least one collected test carrying the id in its name, and that every collected test with an id
refers to an id that exists here. WHY is in [DESIGN-GUIDE](DESIGN-GUIDE.md); HOW in [CONSUMERS](CONSUMERS.md).

Vocabulary. *Target*: the dedicated raw device or partition iobench may write. *Work dir*: a directory on the filesystem under study (S3-S5). *Calibration*:
the slow, once-per-host phase. *Matrix*: the fast iteration reusing a calibration. *Rung*: one working-set size of the ladder. *Steady*: median of the last 25 %
of a time-resolved series. *Cap basis*: the figure caps and the io.cost model may derive from. *Written extent*: the byte ranges this calibration wrote.

## 1. Architecture

* **IOB-ARC-01** Package `iobench` under `libraries/iobench/src/iobench/`. Every module except `iobench.cli` imports only the Python standard library. `iobench.cli` is the only module that may import `cli_extended`. *Oracle:* `test_arc_01_core_imports_stdlib_only` imports every core module in a subprocess whose import hook rejects any non-stdlib top-level name; `test_arc_01_cli_extended_only_in_cli` walks the AST of every module.
* **IOB-ARC-02** All side effects (file reads and writes, sysfs/sysctl writes, subprocesses, clock, sleep, environment, signals) go through the `Host` protocol (`host.py`). Core code MUST NOT call `open`, `subprocess`, `os.*` effect functions, `time.*` or `sys.exit` outside `host.py`, `cli.py` and `__main__.py`. *Oracle:* `test_arc_02_no_direct_effects` is an AST scan with an allowlist; adding `open(` to a core module turns it red.
* **IOB-ARC-03** `iobench.testing` ships `FakeHost` (in-memory filesystem, sysfs/procfs tree, `/proc/diskstats`, mountinfo, swaps, clock) and `FakeFio` (a scenario-driven `fio` replacement returning fio-3.x JSON and logs). Scenario knobs: per-rung rate, cache cliff (`C`, `F`, `R`), burst-then-throttle, flat cap, slow disk, fresh-block penalty, byte or IOPS metering, noise, a crash at stage N. *Oracle:* `test_arc_03_fakefio_scenarios` runs each knob and asserts the emitted JSON parses with the production parser and that the knob is observable in the output.
* **IOB-ARC-04** Determinism: with the same Host responses and clock a run produces byte-identical `result.json` (key order fixed, no wall-clock reads outside `Host.now`). *Oracle:* `test_arc_04_deterministic_result` runs twice and compares bytes.
* **IOB-ARC-05** fio is invoked only through `fio.py` builders; every argv is built from typed parameters and recorded (without host paths of secrets) in the evidence. No shell. *Oracle:* `test_arc_05_fio_argv_recorded_no_shell`.

## 2. Target guard (finding P-01)

* **IOB-TGT-01** `iobench target init DEV` MUST refuse, exit 2, write nothing, and name the reason, when the device (or any partition or holder of it, or the parent disk of a partition) is: mounted (`/proc/self/mountinfo`), swap-active (`/proc/swaps`), carries a filesystem/partition/LVM/RAID signature (`blkid -p` or `wipefs -n` non-empty) other than an iobench marker, or is the disk or partition containing `/` or `/boot`. *Oracle:* `test_tgt_01_refuses_{mounted,swap,signature,root,parent_of_mounted,holder}` (one controlled-wrong case each; each asserts no byte written to the FakeHost device and the message names the condition).
* **IOB-TGT-02** The override is `--i-own-this-device` **plus** `--expect-size BYTES` equal to the actual size **plus** `--expect-partuuid ID` equal to the actual PARTUUID (or `--expect-serial` when the device reports a non-empty serial; the r1002 virtio disk reports none). All three are required; a mismatch is refused. The override only lifts the *signature* refusal; mounted, swap-active, and root refusals are never overridable. *Oracle:* `test_tgt_02_override_matrix` (flag subsets x match/mismatch x each refusal kind).
* **IOB-TGT-03** The marker is a 1 MiB block at the **end** of the device (magic, schema, size, partuuid, created time, optional `fs_uuid` of an iobench-made filesystem). Benchmarks MUST NOT write the last 1 MiB, nor the first 1 MiB if the device is also a filesystem target; an iobench-made filesystem is created with an explicit size excluding the last 2 MiB. *Oracle:* `test_tgt_03_marker_layout_and_reserved_tail` (any fio job extent overlapping the reserved tail fails the test).
* **IOB-TGT-04** Every command that writes the device (`calibrate`, `matrix` when it uses the raw device) MUST verify before each stage that the marker exists and its size/partuuid still match the live device, and that the signature set equals exactly {iobench marker} or {iobench marker, the ext4 whose uuid the marker records}. *Oracle:* `test_tgt_04_{no_marker,size_changed,partuuid_changed,foreign_signature_appears}_refused`.
* **IOB-TGT-05** A work dir MUST be new or empty, or contain only iobench's own `.iobench-workdir` marker; it MUST have free space >= max(4 GiB, 3 x the planned volume); files are created with explicit byte caps and removed at the end. A work dir on the root filesystem is allowed only with `--confirm-quiet-window` (that is the live-host use case). *Oracle:* `test_tgt_05_workdir_rules`.

## 3. Time-series reduction (findings B-01, B-02, B-03)

* **IOB-SER-01** `reduce_series(points)` takes `(t_seconds, rate)` pairs and MUST raise `SeriesError` (never guess) for: fewer than 8 points; any NaN, infinity, or negative value; timestamps that are not strictly increasing; a zero or non-positive steady value. *Oracle:* `test_ser_01_refusals` (NaN at index 3, 20 samples all at t=0, duplicates, negative, 7 points, all-zero).
* **IOB-SER-02** Steady = median of the last 25 % (min 3 points). The knee is the first index after which the **rolling median (window 5)** stays within +-15 % of steady; it is `None` if no such index exists in the first 90 % of the series. *Oracle:* `test_ser_02_noisy_tail_knee`: 10 samples at 100000 then 30 alternating 16000/24000 around 20000 (true knee at 50 s) MUST give knee within +-10 s of 50 and a burst that does not include the noise above steady (finding B-01); `test_ser_02_clean_step`.
* **IOB-SER-03** `state` is one of `steady` (tail flat, knee found), `flat` (flat from the start: "no burst observed", never "no burst exists"), `unsteady` (tail CV > 0.20 or trend > 0.20 of steady across the tail). `steady_certified` is true for `steady` and `flat` only. The old name `passed_knee` MUST NOT exist (finding B-02). *Oracle:* `test_ser_03_states` (falling run -> `unsteady`; flat -> `flat` and the reason text contains "does not prove"); `test_ser_03_no_passed_knee_name` greps the package.
* **IOB-SER-04** `rate_limited` is true when the coefficient of variation over any window of >= 60 s (or the whole series if shorter than 120 s) is < 1 %. It is informational and is never a refusal. *Oracle:* `test_ser_04_flat_line` (4096 MiB/s +-1 -> true; the noisy 21.6k IOPS series -> false).
* **IOB-SER-05** `burst_units` = integral of (smoothed rate - steady) over samples before the knee, in rate x seconds; `mean_overstates(reading, ratio)` takes the ratio as a required argument (no literal). *Oracle:* `test_ser_05_burst_units`.

## 4. Calibration

* **IOB-CAL-01** The ladder rungs are 1 GiB, 2, 4 ... doubling up to the largest feasible working set `WS_max = min(0.85 x usable target, R_est x fill_budget_s)`. After the first rung that falls below the cliff threshold the calibration inserts one midpoint rung (cliff refinement) if the budget allows. *Oracle:* `test_cal_01_rung_plan` (target 406 GiB -> rungs ... 256, 406; with a cliff scenario at 270 GiB a 331 GiB-class midpoint is added; fill-bounded plan on a slow disk).
* **IOB-CAL-02** Read-only-written: the calibration first writes the extent `[0, WS_max)` sequentially once (1 MiB, QD16, `fill_bps` recorded) and every read job is confined to a prefix of that extent; the Host records a written-extent map and a read of an unwritten byte is a hard test failure and, in production, an error. *Oracle:* `test_cal_02_reads_only_written_extent` (FakeHost raises on an unwritten read; also a controlled-wrong mutation that shifts a read job beyond the fill).
* **IOB-CAL-03** Each rung runs random 4 KiB read and write at QD32 for >= 30 s (the first 5 s discarded) with a 2-5 s log; the largest rung additionally QD1 and QD8. Each series goes through `reduce_series`; the rung record carries `steady_iops`, `mean_iops`, `state`, `rate_limited`, latency p50/p90/p95/p99/p99.9, and a series downsampled to <= 120 points. A rung that is `unsteady` is extended up to 5 min, then kept as `unsteady` and never used as a basis. *Oracle:* `test_cal_03_rung_record_and_extension`.
* **IOB-CAL-04** Cliff: `cached_ref` = median steady IOPS of the first three rungs; the cliff rung is the first rung whose steady value is < 0.75 x `cached_ref` and where no later rung recovers to >= 0.75 x `cached_ref`; `cliff_bytes` is that rung's size. If none, `cliff_bytes` is null and the result carries `cliff_not_observed`. Rungs below the cliff are class `cached_tier`, at/after it class `post_cliff`. *Oracle:* `test_cal_04_cliff_detection` on four scenarios (cliff at 270 GiB, no cliff, cliff only at the last rung, noisy small rungs) and on the **r1002 replay** (cliff between 250 and 300 GiB).
* **IOB-CAL-05** Cache model: from the post-cliff rungs (>= 3 needed) fit `iops(ws) = R / (1 - C/ws)` for `ws > C` by least squares in log space with `F` = the mean of the cached-tier rungs; report `C_bytes`, `R_iops`, `r2`, `fit: good|poor|insufficient`. `good` iff `r2 >= 0.95`, >= 3 post-cliff rungs, and `C` within [0.1, 0.95] of the device. *Oracle:* `test_cal_05_cache_fit_r1002_replay` (rungs 300/350/400 GiB read 46315/30883/23922, F ~ 92k: C in [230, 260] GiB, R in [8.5k, 10.5k]); `..._insufficient_with_two_rungs`; `..._poor_on_random_data`.
* **IOB-CAL-06** Cap basis per direction: if `fit == good` then `min(measured_largest, R_iops)`, else `measured_largest` plus `cache_residual_possible`. A `cached_tier` figure, an `unsteady` rung, and a `mechanics-only` figure MUST NOT be a cap basis. *Oracle:* `test_cal_06_cap_basis` (controlled-wrong: remove all post-cliff rungs -> no basis, `cap_basis: null`).
* **IOB-CAL-07** Fresh versus rewrite: where the target is an iobench-marked device (or `--installer`) and `blkdiscard` is supported, the calibration discards a dedicated extent and records random 4 KiB QD32 writes to it as `fresh`, and to a written extent as `rewrite`, with `fresh_penalty = rewrite/fresh`. Otherwise `fresh_write: not_measured` with the reason. *Oracle:* `test_cal_07_fresh_penalty` (scenario fresh 3596 vs rewrite 71584 -> penalty 19.9 and `metering: fresh-penalty` input; unsupported discard -> `not_measured`, never a number).
* **IOB-CAL-08** `QD90` = smallest tested queue depth whose steady IOPS >= 0.9 x the maximum over QD1/8/32 at the largest rung. *Oracle:* `test_cal_08_qd90` (r1002: 23.7k at QD8 vs 23.9k at QD32 -> 8).
* **IOB-CAL-09** `calibration.json` is written atomically only when the calibration is `complete`; an `aborted` or failed calibration writes `calibration.last-failed.json` and never replaces a current good one. It records the identity, the extent map, the budget used and each stage status. *Oracle:* `test_cal_09_atomic_and_no_replace`.

## 5. Slow disks and time-bounded work

* **IOB-SLO-01** S1 rate probe: 20 s buffered sequential write of a new file in the work dir (or direct write to the target) sampled each second; `R_est` = the median of the last 10 samples. *Oracle:* `test_slo_01_rate_probe`.
* **IOB-SLO-02** No stage may write more than `1.2 x R_est x stage_seconds` bytes nor exceed the free-space bound of `IOB-TGT-05`. *Oracle:* `test_slo_02_volume_bound` (a 14 MB/s scenario over a full `standard` plan: the Host's byte counter is below the bound at every stage).
* **IOB-SLO-03** If `R_est < 5 MB/s`, or the fill cannot fit its budget share, the calibration runs time-bounded from the measured rate: the largest rung is the bound of `IOB-CAL-01`, `cache_proof` is `limited-by-budget`, and the result says why. The sweep is skipped only if the quiet gap cannot drain the dirty pool within its bound (then proposals are `formula-only`). *Oracle:* `test_slo_03_slow_disk_time_bounded` (R_est 3 MB/s; assert nonzero measured figures, `limited-by-budget`, no stage over budget).
* **IOB-SLO-04** The whole run has a hard wall-clock budget; stages run in the priority order S0, S1, S3, S4, S5, S2, S6, S7; a stage that cannot fit its minimum is skipped and recorded `skipped: budget`, never shortened silently. *Oracle:* `test_slo_04_budget_priority_and_skip_record`.

## 6. Drain rate, sweep, read cost

* **IOB-DRN-01** `R_drain` = the median of the last 25 % of 1 s samples of device write bytes/s (from `/proc/diskstats`) while the reference load runs 60-90 s in the work dir. The reference load is: a buffered writer creating new 1 GiB files (removed behind itself, volume-bounded) plus a 4 KiB random-write job with `fsync=16`, 2 jobs. `rates_consistent` is true iff `R_drain` is within 1.3x of the ladder's `min(rewrite, fresh)` write figure; else `drain_below_ladder` (or `drain_above_ladder`, which is suspicious). *Oracle:* `test_drn_01_drain_rate_and_consistency`.
* **IOB-SWP-01** The sweep cells are REF (formula values), up to five neighbours (dirty x2 and /2, `nr_requests` /2 and x2, `max_sectors_kb` one step each way), and REF again last; cell length 60-90 s; each cell is preceded by a quiet gap that ends when dirty+writeback < 4 MiB for 5 s or after 60 s, recorded. Total for 6 cells <= 15 min of planned time. *Oracle:* `test_swp_01_plan_and_gap_bound`.
* **IOB-SWP-02** A probe unit appends 200 B and calls `fdatasync` every 0.5 s on the work dir's filesystem and records per-op latency; it runs as a transient unit in a slice without IO limits and with `RuntimeMaxSec`. *Oracle:* `test_swp_02_probe_unit_properties`.
* **IOB-SWP-03** Noise floor = max/min of REF's two probe p99 values. A neighbour replaces REF only if its probe p99 beats REF's by more than the noise ratio in its cell **and** in the repeat, and the gate-like load's fsync p99 and iops did not regress by more than the noise ratio. Otherwise REF stands. A noise floor > 4 marks the sweep `inconclusive`. *Oracle:* `test_swp_03_decision_rule` (a lucky single cell does not win; a consistent 5x winner does; noise 5 -> inconclusive).
* **IOB-SWP-04** All knob writes go through the restore journal (`IOB-SAF-01`); the sweep sets only `vm.dirty_bytes`, `vm.dirty_background_bytes`, `queue/nr_requests`, `queue/max_sectors_kb` of the derived disk. *Oracle:* `test_swp_04_only_allowed_knobs` (FakeHost write log).
* **IOB-RDC-01** For each candidate `max_sectors_kb` (and the device's `max_hw_sectors_kb`) run direct 1 MiB sequential reads at QD4 for 10 s over the written extent; `read_ratio` = candidate / `max_hw` bytes/s; also one buffered sequential read pair (64 vs 256) recorded. *Oracle:* `test_rdc_01_read_ratio` (replay: 1.3 GB/s vs 0.27 GB/s -> 0.21).

## 7. Cost shape and latency

* **IOB-SHP-01** S6 grid: sizes {4 KiB, 16 KiB, 64 KiB, 256 KiB, 1 MiB} x {sequential, random} x {read, write} x {rewrite, fresh (if `IOB-CAL-07` allows)} at QD32 on the cap-basis working set, 30 s per cell, steady tail via `reduce_series`; a half-rate cell (`--rate` at 50 % of that size's saturated byte rate) is added for the 4 KiB and 64 KiB random cells to give the latency at equal offered load. Each cell record carries `{dir, pattern, bs, block_state, offered_bps, steady_bps, steady_iops, lat_us{p50,p90,p99}, cv, flat_line}`. *Oracle:* `test_shp_01_grid_plan_and_cell_fields`.
* **IOB-SHP-02** Per `dir x pattern x block_state` fit `1/steady_iops = c_io + bs x c_byte` by linear regression over the five sizes (units: microseconds per IO and nanoseconds per byte), and report `r2` and `residual_max` (largest relative residual). `linear_model_fit` is `good` iff `r2 >= 0.9` and `residual_max <= 0.25`, else `poor`. *Oracle:* `test_shp_02_fit` (synthetic `c_io = 80 us, c_byte = 0.25 ns` recovered within 2 %; noise-only data -> `poor`).
* **IOB-SHP-03** Metering classification (by rewrite cells): `byte` if CV of `steady_bps` across sizes <= 0.15 and CV of `steady_iops` >= 0.40; `iops` if the converse; `fresh-penalty` if the fresh/rewrite IOPS ratio at 4 KiB >= 3 (this takes precedence); `mixed` if the fit is good with `c_io > 0` and `c_byte > 0` and neither CV rule holds; `undetermined` otherwise. *Oracle:* `test_shp_03_metering_classes` (one scenario per class, plus a controlled-wrong near-threshold case per boundary).
* **IOB-SHP-04** The six model keys derive from the fit only when `linear_model_fit == good` for that direction: `*bps` = 1e9/`c_byte` bytes/s (limited to the cap-basis steady), `*seqiops` / `*randiops` = 1e6/`c_io`; byte-metered directions get `seqiops = randiops = bps/4096`. When the fit is poor the keys are `{"value": null, "reason": "linear_model_fit: poor"}`, never a guess. *Oracle:* `test_shp_04_model_keys_and_poor_fit_refusal`.
* **IOB-LAT-01** S7: at 10, 30, 60 and 90 % of the cap-basis ceiling (random 4 KiB, `--rate_iops`), per direction, record p50/p90/p95/p99 completion latency and the achieved rate; `light_p90`/`light_p95` are the 10 % and 30 % points (the larger of the two). A load point whose achieved rate is < 90 % of offered is flagged `not_reached`. *Oracle:* `test_lat_01_curve_and_light_points`.
* **IOB-LAT-02** The latency knee is the first load point whose p99 is >= 2 x the 10 % point's p99; reported, or null. *Oracle:* `test_lat_02_knee` (r1002 uncached replay: knee at bg QD8-16 equivalent).

## 8. Ceiling history (S8)

* **IOB-CEI-01** `ceilings.jsonl` is append-only, one JSON object per line `{ts, dir, plateau_bps, duration_s, source}` with `source` in `benchmark|governor|probe`; a first header line carries the disk identity digest. Appends are atomic (single `write` + `fsync`). *Oracle:* `test_cei_01_append_only_atomic`.
* **IOB-CEI-02** An observation with `plateau_bps <= 0`, `duration_s < 60`, a non-finite value, or an unknown `source` is refused. *Oracle:* `test_cei_02_refusals`.
* **IOB-CEI-03** `ceilings summary` returns worst (minimum), median, best (maximum) and count per direction over a window; with no observations it returns `history: "single-run"` and the caller uses the run's own windows. History whose identity digest does not match the live disk is ignored with a warning. *Oracle:* `test_cei_03_summary_and_identity`.
* **IOB-CEI-04** `recalibrate_recommended` (a warning in `check`, never an invalidation) when the median plateau of the last 12 h differs from the calibration's by more than 25 % in two consecutive 12 h windows. *Oracle:* `test_cei_04_recalibrate_rule`.
* **IOB-CEI-05** A complete run appends one `benchmark` observation per direction per sustained-plateau window (>= 60 s, state `steady`/`flat`) of its largest rung. *Oracle:* `test_cei_05_run_appends_plateaus`.

## 9. Matrix

* **IOB-MAT-01** `matrix` refuses (exit 2, "no current calibration") unless `calibration.json` exists, is schema-supported and its identity matches. *Oracle:* `test_mat_01_requires_current_calibration` (absent, stale identity, corrupt).
* **IOB-MAT-02** Default plan: S0, S3, the 6-cell S4 sweep and S5; planned time <= 15 min. `--knob NAME --candidates a,b[,c] --repeat` plans REF + candidates + repeat of the better, planned time <= 4 min for two candidates. *Oracle:* `test_mat_02_plan_budgets`.
* **IOB-MAT-03** The matrix uses only the calibrated written extent and calibrated figures; it MUST NOT recompute caps or the model, it copies the calibration's basis into the result with the calibration id. *Oracle:* `test_mat_03_reuses_calibration`.
* **IOB-MAT-04** Recalibration triggers: an identity change (`check` exit 3) and `recalibrate_recommended`. A matrix run on a stale calibration refuses; on `recalibrate_recommended` it warns and proceeds. *Oracle:* `test_mat_04_recalibration_triggers`.

## 10. Derivation

All proposals live in `proposals.*` and carry `{value, rule, inputs, confidence, flags[]}`; a value is a number or `"kernel"`.

* **IOB-DER-01 (R1)** `dirty_bytes` = largest power-of-two MiB <= `W x R_drain - inflight`, `inflight = nr_requests x max_sectors_kb x 1024`, clamped to [4 MiB, 25 % of the kernel's effective dirty threshold]; `"kernel"` if the unclamped value >= that threshold. `dirty_background_bytes` (R1b) = `dirty_bytes / 4`. *Oracle:* `test_der_01_r1` incl. replay: gstammtisch `R_drain` 10.5 MB/s, q16, r64 -> 16 MiB / 4 MiB; r1002 -> `"kernel"`.
* **IOB-DER-02 (R2)** `nr_requests` = `2 x QD90` rounded to a power of two in [8, 64]; `"kernel"` if not below the current value. *Oracle:* `test_der_02_r2`.
* **IOB-DER-03 (R3, R4)** `max_sectors_kb` per DESIGN-GUIDE s5 (R3); R4: the chosen triple's REF probe p99 must be <= `P` else `target_missed` is recorded with the closest cell and the value is still shown. *Oracle:* `test_der_03_r3_r4` incl. gstammtisch replay -> 64 (with `read_floor_violated` when `read_ratio < F`) and the 128 KB fallback case.
* **IOB-DER-04 (R5)** `caps.dev_aggregate` = `U x` the cap-basis steady ceiling per key (`RIOPS`, `WIOPS`, `RBPS`, `WBPS`); no per-tier caps are emitted. *Oracle:* `test_der_04_r5_aggregate_only`.
* **IOB-DER-05 (R6 model)** model keys per `IOB-SHP-04` on the **worst plateau** basis (ceiling summary `worst`, else the run's lowest steady window): `wbps` = worst write plateau; byte-metered => `wseqiops = wrandiops = wbps/4096`. *Oracle:* `test_der_05_r6_model` (gstammtisch plateau history 6/14/18 MiB/s -> `wbps = 6291456`).
* **IOB-DER-06 (R6 QoS)** `min = 100`, `max = ceil(110 x best / worst)`; `wpct = 90`, `wlat = 2 x light_p90` rounded up to 5 ms and clamped to [10, 50] ms; `rpct = 95`, `rlat = 2 x light_p95` clamped to [2, 20] ms (units: microseconds in the output keys as in the kernel file). The latency histogram source is recorded (`governor` if a histogram file is supplied via `--governor-histogram`, else `benchmark`). *Oracle:* `test_der_06_r6_qos` (best/worst 18/6 -> max 330; light p90 4.2 ms -> `wlat` 10 ms floor; 17 ms -> 35 ms).
* **IOB-DER-07** Every proposal has `rule` and `inputs` that suffice to recompute it; `confidence` is one of `measured+confirmed`, `measured`, `ratio-inferred`, `formula-only`, `budget-limited`. *Oracle:* `test_der_07_recomputable` recomputes every value from its `inputs` and compares.
* **IOB-DER-08** No proposal derives from a figure that is `cached_tier`, `unsteady`, `mechanics-only`, or from a stage with status other than `ok`; the field becomes `{"value": null, "reason": ...}`. *Oracle:* `test_der_08_no_derivation_from_bad_basis` (one controlled-wrong input per class).

## 11. Plausibility

`quality.plausible` is true only when every check below that applies passed or is informational. Each check has a stable id, appears in `quality.checks[]` as `{id, status: pass|fail|info|not_applicable, detail}`, and has a **controlled-wrong** oracle: a scenario built to violate it, asserting the exact `fail` and the refusal of the derivation that depends on it. `rotational` plays no role.

| Id | Check | Fails when |
|---|---|---|
| **IOB-PLA-01** | tail flat | the cap-basis rung `state == unsteady` |
| **IOB-PLA-02** | working-set rule | the cap-basis rung < `max(0.85 x usable target, 1.2 x cliff)` (unless `limited-by-budget`, which passes with that label) |
| **IOB-PLA-03** | cached-tier factor | `small_rung / largest_rung > 1.5` and the derivation used the small rung (always excluded; factor is reported) |
| **IOB-PLA-04** | mean vs steady | `mean > 1.5 x steady` and the mean was used (the mean is dropped; `mean_overstates` recorded) |
| **IOB-PLA-05** | flat-line | informational: `rate_limited` flagged (r1002 read 4096 MiB/s) |
| **IOB-PLA-06** | Little's law | `iops x mean_clat / QD` outside [0.7, 1.3] for a rung (a broken or faked fio) |
| **IOB-PLA-07** | bytes consistency | random 4 KiB bytes/s exceeds sequential bytes/s of the same direction by > 5 % |
| **IOB-PLA-08** | read-only-written | any read touched an unwritten extent (hard error) |
| **IOB-PLA-09** | fill vs sequential | `fill_bps` and the sequential 1 MiB rewrite bps differ by > 5x |
| **IOB-PLA-10** | cache model | `fit` is `poor` or `insufficient` -> `cache_residual_possible` (info-level warning, caps from measured largest) |
| **IOB-PLA-11** | shape fit | `linear_model_fit == poor` -> model keys not derived |
| **IOB-PLA-12** | noise floor | sweep noise > 4 -> sweep `inconclusive` |
| **IOB-PLA-13** | fresh vs rewrite | `fresh_iops > 1.5 x rewrite_iops` (impossible without a cache effect) |
| **IOB-PLA-14** | mechanics-only | the run was a `vm-test`/`--mechanics-only` run: figures are labelled and never a basis |
| **IOB-PLA-15** | identity stable | the disk/fs identity at the end differs from the start (run rejected) |
| **IOB-PLA-16** | data integrity | NaN, duplicate or non-monotonic timestamps in any series (run rejected) |
| **IOB-PLA-17** | outside history | this run's steady differs from the ceiling history median by > 3x (warning, not a failure) |
| **IOB-PLA-18** | **evidence plausibility** | the cache-proof class is checked against known facts in the replay suite (r1002: cliff 250-300 GiB, fresh 3.6k vs rewrite 71k, 4096 MiB/s read cap) |

*Oracles:* `test_pla_NN_controlled_wrong` for each row (NN = id suffix) and `test_pla_all_pass_on_good_scenario`. PLA-18 is the replay `test_pla_18_r1002_facts`.

## 12. Safety

* **IOB-SAF-01** Before the first state change the tool writes `/run/iobench/restore.json` (temp + rename + fsync) with every original value and its path (`vm.dirty_bytes`, `vm.dirty_background_bytes`, `vm.dirty_ratio`, `vm.dirty_background_ratio`, `queue/nr_requests`, `queue/max_sectors_kb`, `queue/scheduler`, `queue/nomerges` of the derived disk). *Oracle:* `test_saf_01_journal_before_first_write`.
* **IOB-SAF-02** Restore runs on normal exit, exceptions, SIGTERM/SIGINT/SIGHUP, and at the start of the next invocation if a journal exists (`iobench restore` is idempotent and public). The transient units carry `RuntimeMaxSec`. *Oracle:* `test_saf_02_restore_on_{exit,exception,signal,next_start}`.
* **IOB-SAF-03** Restore is verified by read-back; a mismatch is exit 9 naming the file and the expected value, and the journal is kept. *Oracle:* `test_saf_03_readback_mismatch_exit_9`.
* **IOB-SAF-04** The tool reads every slice's `io.max` (raw cgroup file) before and after and records `slice_io_max_unchanged`; a difference is exit 9. The Host refuses (raises) any write to a path ending `io.max`, any `systemctl set-property` or `daemon-reload`, and any `IO*Max=`/`IOWeight=` argument. *Oracle:* `test_saf_04_io_max_never_written` (a stage that tries it fails the test); `test_saf_04_io_max_changed_by_others_detected`.
* **IOB-SAF-05** Live host (default profile): refuse (exit 2) with running production containers (named by `--production-pattern`, default: any running container) unless `--confirm-quiet-window`; `--installer` bypasses this. *Oracle:* `test_saf_05_refuses_with_containers`.
* **IOB-SAF-06** Abort rule: root `io.pressure` `some avg10` of the cgroup named by `--protect-cgroup` above `--abort-psi` for 3 consecutive samples, or the abort file `/run/iobench/abort`, aborts the run, restores, and marks it `aborted` (exit 8). *Oracle:* `test_saf_06_abort_paths`.
* **IOB-SAF-07** An aborted or failed run never replaces `result.json` or `calibration.json`. *Oracle:* `test_saf_07_failed_run_keeps_good_result`.
* **IOB-SAF-08** At most one iobench run per host at a time (lock file, non-blocking; a second invocation exits 2 and writes no record). *Oracle:* `test_saf_08_single_instance`.

## 13. Result, calibration and read path

* **IOB-RES-01** `result.json`, `calibration.json` and `ceilings.jsonl` follow section 14; files are written atomically (temp + rename + fsync of file and directory), mode 0644 root-owned. *Oracle:* `test_res_01_atomic_write_modes`.
* **IOB-RES-02** Versioning: integer `schema` (major) and `schema_minor`. A reader MUST refuse an unknown major (exit 5), ignore unknown keys, and accept any minor <= the one it knows (missing newer optional keys are `not_measured`, never a default value). *Oracle:* `test_res_02_versioning` (major+1 refused; extra key ignored; older minor read).
* **IOB-RES-03** Identity-based validity, not age: current iff `identity.disk.{devno,model,serial,partuuid,size_bytes}`, `root_source`, `fs.uuid` (if any), and the kernel `major.minor` match the live system. `scheduler`, `ram_bytes`, age (> 180 days) are advisory `warnings`. The identity is re-read at the end of the run; a change rejects the run (`IOB-PLA-15`). *Oracle:* `test_res_03_identity_validity` (each identity field flipped -> `stale` exit 3; each advisory flipped -> warning only).
* **IOB-CHK-01** `iobench check [--result P] [--calibration] [--json]`: exit 0 current and plausible, 3 stale identity, 4 absent, 5 corrupt or unsupported schema, 6 only an aborted record exists, 7 identity current but `quality.plausible` false. JSON: `{state, reasons[], warnings[], result, calibration_id}`. "stale" is reachable only from an identity mismatch; "could not read" is exit 5 with the OS error, never a default. *Oracle:* `test_chk_01_exit_codes` (one case per code) and `test_chk_01_status_collapse_audit` (table of real-world conditions per status, asserted both directions).
* **IOB-CHK-02** `iobench show --proposals|--evidence|--calibration|--ceilings [--section S] --json` prints the corresponding object; unknown section => exit 2. It never recomputes and never modifies. *Oracle:* `test_chk_02_show_is_read_only_and_exact`.
* **IOB-CHK-03** The wizard contract: precedence `benchmark (check == 0) > configured > kernel`; the guaranteed keys for schema 1 are `proposals.writeback.{dirty_bytes,dirty_background_bytes,nr_requests,max_sectors_kb}`, `proposals.caps.dev_aggregate`, `proposals.iocost_model`, `proposals.iocost_qos`, `quality`. *Oracle:* `test_chk_03_guaranteed_keys_present_on_golden_results` for both replay results.
* **IOB-CHK-04** `IOBENCH_DIR` (default `/var/lib/iobench`) and `--state-dir` select the directory; the default is a policy choice, every path is printed by `show --paths`. *Oracle:* `test_chk_04_state_dir_resolution`.

## 14. Schemas

All timestamps are UTC ISO-8601 `Z`; sizes are bytes; rates are bytes/s or IO/s; latencies microseconds.

### 14.1 `result.json` (schema 1, minor 0)

```json
{
  "schema": 1, "schema_minor": 0,
  "tool": {"name": "iobench", "version": "0.1.0", "method": "ladder-v1+cachefit-v1+shape-v1+latcurve-v1+drain-v1+sweep-v1+readcost-v1"},
  "run": {"id": "run-20261021T031500Z-a1b2", "started": "2026-10-21T03:15:00Z", "finished": "2026-10-21T03:27:41Z", "profile": "matrix", "status": "complete",
          "budget_s": 900, "calibration_id": "cal-20261021T020000Z-7b2d0c1e",
          "stages": {"S0": "ok", "S1": "ok", "S3": "ok", "S4": "ok", "S5": "ok"}},
  "identity": {"disk": {"devno": "254:7", "model": null, "serial": null, "partuuid": "7b2d0c1e-07", "size_bytes": 435853320192, "scheduler": "none", "rotational_flag": "1"},
               "root_source": "/dev/vda3", "fs": {"type": "ext4", "uuid": "..."}, "kernel": "7.2.9", "ram_bytes": 8321499136, "virt": "kvm"},
  "targets": {"W_s": 2.0, "P_ms": 250, "F_read_ratio": 0.25, "U_dev_share": 0.70, "source": "default"},
  "evidence": {"rate_probe": {}, "drain": {}, "sweep": {"noise_floor_ratio": 1.9, "cells": []}, "read_cost": {"native_bps": 0, "candidates": []},
               "preflight": {"containers_active": 0, "confirmed_quiet_window": true, "slice_io_max_before": {}, "slice_io_max_after": {}}},
  "proposals": {"writeback": {}, "caps": {}, "iocost_model": {}, "iocost_qos": {}, "notes": [], "target_missed": null},
  "quality": {"plausible": true, "cache_proof": "working-set", "checks": [{"id": "IOB-PLA-01", "status": "pass", "detail": ""}], "limits": []},
  "restore": {"verified": true, "slice_io_max_unchanged": true, "journal_removed": true}
}
```

Required top-level keys: `schema, schema_minor, tool, run, identity, targets, evidence, proposals, quality, restore`. `run.status` in `complete|aborted|failed`. `quality.cache_proof` in `none|working-set|limited-by-budget`.

### 14.2 `calibration.json` (schema 1)

Same envelope (`schema, schema_minor, tool, run, identity, quality`) plus:

```json
{
  "calibration": {"id": "cal-20261021T020000Z-7b2d0c1e", "target": {"kind": "device", "path": "/dev/vda7", "size_bytes": 435853320192, "usable_bytes": 435852271616},
    "extent_written": [[0, 435852271616]], "fill_bps": 1357000000, "R_est_bps": 1357000000,
    "ladder": {"rungs": [{"ws_bytes": 1073741824, "dir": "read", "qd": 32, "steady_iops": 97850, "mean_iops": 98100, "state": "steady", "rate_limited": false, "class": "cached_tier", "lat_us": {"p50": 309, "p90": 420, "p95": 480, "p99": 545, "p999": 692}, "series": [[0, 97000]]}],
               "cliff_bytes": 322122547200, "cached_ref_iops": 92000, "qd90": 8, "idle_qd1_p99_us": {"read": 272, "write": 440}},
    "cache_model": {"read": {"C_bytes": 262000000000, "R_iops": 9400, "F_iops": 92000, "r2": 0.99, "fit": "good"}, "write": {"fit": "insufficient"}},
    "cap_basis": {"read": {"iops": 9400, "source": "cache_model.R", "flags": []}, "write": {"iops": 21000, "source": "measured_largest", "flags": ["cache_residual_possible"]}},
    "fresh_vs_rewrite": {"measured": true, "fresh_iops": 3596, "rewrite_iops": 71584, "penalty": 19.9},
    "shape": {"cells": [], "fit": {"read": {"rand": {"c_io_us": 0, "c_byte_ns": 0, "r2": 0, "residual_max": 0}}}, "metering": "fresh-penalty", "linear_model_fit": "good"},
    "latency_curve": [{"dir": "write", "load_pct": 10, "bps": 0, "lat_us": {"p50": 0, "p90": 0, "p95": 0, "p99": 0}}],
    "latency_knee": {"read": null, "write": null},
    "iocost_keys": {"rbps": 0, "rseqiops": 0, "rrandiops": 0, "wbps": 0, "wseqiops": 0, "wrandiops": 0}}
}
```

### 14.3 `ceilings.jsonl`

```json
{"header": true, "schema": 1, "identity_digest": "sha256:..."}
{"ts": "2026-10-21T03:20:00Z", "dir": "write", "plateau_bps": 14680064, "duration_s": 600, "source": "governor"}
```

### 14.4 Restore journal and marker

`/run/iobench/restore.json`: `{"run": id, "started": ts, "entries": [{"path": "...", "original": "...", "applied": "..."}]}`. Marker: the last 1 MiB of the target, first 4 KiB a JSON object `{"magic": "IOBENCH-TARGET-1", "size_bytes": N, "partuuid": "...", "created": ts, "fs_uuid": null}` followed by zero padding; the remaining bytes are never read.

## 15. Profiles, stages, budgets

| Profile | Command | Stages | Planned time |
|---|---|---|---|
| `quick` | `calibrate --profile quick` | S0, S1, 3-rung ladder, S6 reduced (64K and 4K only), S7 at 10/30/90 % | ~12 min |
| `standard` | `calibrate --profile standard` (default) | S0, S1, full ladder + cliff refinement + cache model + fresh/rewrite + S6 + S7 | ~30 min (hard budget) |
| `thorough` | `calibrate --profile thorough` | standard + QD64 + 20 min tail run + idle/refill 60 s + extra sweep midpoint rungs | ~75 min |
| `matrix` | `matrix` | S0, S3, S4 (6 cells), S5 | 10-15 min |
| `matrix --knob` | `matrix --knob K --candidates ...` | S0, S3, REF + candidates + repeat | 3-4 min |
| `run` | `run` | calibrate if no current calibration, then matrix | sum |

* **IOB-PRF-01** `--budget-minutes N` overrides a profile; the plan (`--dry-run`) prints each stage's allotted seconds, fio argv and the written-byte bound, and writes nothing. *Oracle:* `test_prf_01_dry_run_plan_is_complete_and_writes_nothing`.
* **IOB-PRF-02** The `quick` profile marks the result `quality.limits += ["quick-profile"]`; proposals from it carry `confidence: budget-limited` where a stage was reduced. *Oracle:* `test_prf_02_quick_limits`.

## 16. CLI

* **IOB-CLI-01** Commands: `target init|show`, `calibrate`, `matrix`, `run`, `check`, `show`, `ceilings add|summary`, `restore`, `--version`. All mutating commands support `--dry-run`. Exit codes: 0 ok, 1 runtime failure, 2 refusal or usage, 3-7 as `IOB-CHK-01`, 8 aborted (restored), 9 restore or io.max verification failure. *Oracle:* `test_cli_01_commands_and_exit_codes`.
* **IOB-CLI-02** Every refusal message names the condition and the correct remedy; it MUST NOT prescribe a remedy that is itself unsafe (no "rerun with --force" for an override that does not exist). *Oracle:* `test_cli_02_messages_name_condition_and_safe_remedy`.
* **IOB-CLI-10** `iobench.cli` is registered with `cli_extended` (`CliRegistry`, generated help, `--dry-run` on mutating verbs, version from the distribution). The generated CLI surface and review artefacts exist and pass the cli-extended audit lane. *Oracle:* the cli-extended `audit` test module plus `test_cli_10_surface_matches_registry`.

## 17. Test and gate plan

* **IOB-EVD-01 (replay fixtures)** `tests/fixtures/` holds condensed, provenance-stamped fixtures: the r1002 ladder and shape-relevant runs from `IO-BENCH-01-data/r1002-fio-out.tgz` (sha256 recorded in the fixture header) and the gstammtisch matrix tables (HOST-IO-INTERFERENCE s4.3/4.4). Required replay oracles: gstammtisch -> 16 MiB / 4 MiB / 16 / 64; r1002 -> `kernel` for dirty and sectors and cliff in [250, 300] GiB; cached-tier factor in [3.5, 4]; the cache fit of `IOB-CAL-05`. *Oracle:* the named tests, which fail when a rule constant is edited.
* **IOB-EVD-02** A fixture-only round trip is not an oracle: every replay test asserts a derived value that depends on a rule. *Oracle:* `test_evd_02_mutation_sanity` mutates each rule constant (via monkeypatch) and asserts the corresponding replay test fails.
* **IOB-VM-01** The VM lane (`testing/vm/vmctl`, QEMU/TCG) proves mechanics only: marker init and guard refusals on a real small virtio disk, mkfs/mount, fio runs, sysctl/sysfs writes land and are restored, `result.json` passes `check`. Its results are labelled `mechanics-only` and `IOB-DER-08` forbids deriving from them. *Oracle:* `test_vm_01_*` (marked `vm`, run in the VM lane, not in the unit lane).
* **IOB-GATE-01** `libraries/iobench/run-gate.toml` registers the lane `r0-r1` (`kind = "command"`, `environment = "tester-unified"`): pytest with `--cov=iobench --cov-branch --cov-fail-under=100` (the measurement code is mostly pure logic; unreachable I/O shims are excluded explicitly in `.coveragerc` with a reason per exclusion) and a `gate` lane aggregating it; the devcontainer is a cockpit and never the gate. *Oracle:* `./run-gate.py r0-r1` exit 0, read from a marker file.
* **IOB-DOC-01** Docs-sync: every requirement id here has a collected test; every collected iobench test id exists here; every cross-document anchor resolves. *Oracle:* `test_doc_01_ids_anchors`.
* **IOB-DOC-02** Every `json` block in the three documents parses with the shipped schema loader as the object it claims (a `proposals` object, a `check` output, a ceilings summary); every `bash` block's `iobench` invocation parses with the CLI's own parser in `--dry-run` mode; every value of the closed vocabularies (`metering`, `confidence`, `fit`, `state`, `cache_proof`, check ids, exit codes) appears in at least one of the three documents. *Oracle:* `test_doc_02_examples_parse_and_vocabulary_complete`.
* **IOB-REL-01** Release through cmru only: `libraries/iobench/cmru.toml` (`iobench-v*` tag family, wheel plus zipapp plus sha256, GitHub Releases only, never PyPI); floors for `fio` (>= 3.30) with the reason documented. *Oracle:* `cmru status` reports the project and `cmru release --dry-run` plans the wheel (acceptance probe at the release work item, not a unit test).

## 18. Out of scope for IOB-03

Consumer wiring (migration phases M1-M4), the persistent long-term probe (D-011), per-tier cap proposals (conflict C-1), and any change to the shipped `iocost_coef_gen.py` flow. The library provides the read path they will use.
