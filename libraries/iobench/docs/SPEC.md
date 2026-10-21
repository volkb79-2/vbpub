# iobench SPEC

Normative. MUST / SHOULD / MAY as in RFC 2119. Each requirement has an id `IOB-<AREA>-<nn>` and an **Oracle**: the test (or, where stated, the acceptance
probe) that goes red when the requirement is violated. Test oracles are named `tests/test_<area>.py::test_<id>_<slug>`. Revision 2 (2026-10-21) folds in
review `review-IOB-03-P1P2.md` and the controller rulings R-1..R-10 recorded in [DESIGN-GUIDE](DESIGN-GUIDE.md) section 11. WHY: DESIGN-GUIDE; HOW: [CONSUMERS](CONSUMERS.md).

Vocabulary. *Target*: the dedicated raw device or partition iobench may write. *Work dir*: a directory on an **existing** filesystem under study (S3-S5);
iobench never creates, mounts or unmounts a filesystem. *Calibration*: the slow once-per-host phase. *Matrix*: the fast iteration reusing a calibration.
*Rung*: one working-set size of the ladder. *Steady*: median of the last 25 % of a time-resolved series. *Written extent*: byte ranges whose write fio itself
reported complete. *Reference cell*: the fixed short cell of section 8.

## 1. Architecture

* **IOB-ARC-01** Package `iobench` under `libraries/iobench/src/iobench/`. Every module except `iobench.cli` imports only the standard library; only `iobench.cli` may import `cli_extended`. *Oracle:* `test_arc_01_core_imports_stdlib_only` (import every core module in a subprocess whose import hook rejects non-stdlib top-level names); `test_arc_01_cli_extended_only_in_cli` (AST).
* **IOB-ARC-02** All side effects (file and sysfs/sysctl access, subprocesses, clock, sleep, environment, signals, randomness and unique tokens) go through the `Host` protocol (`host.py`): `Host.now()`, `Host.sleep()`, `Host.token()` (the only source of the `run.id` suffix), `Host.run()`, `Host.read_text()`, `Host.write_sysfs()`, `Host.write_range()`, `Host.discard_range()`, `Host.append_line()`. Core code MUST NOT call `open`, `subprocess`, `os.*` effect functions, `time.*`, `random.*`, `uuid.*`, `secrets.*` or `sys.exit` outside `host.py`, `cli.py`, `__main__.py`. *Oracle:* `test_arc_02_no_direct_effects` (AST scan with an allowlist; injecting `open(` into a core module turns it red).
* **IOB-ARC-03** `iobench.testing` ships `FakeHost` (in-memory filesystem, sysfs/procfs tree, `/proc/diskstats`, mountinfo of both `self` and `1`, swaps, clock, a **device model with a reserved tail and an extent map**) and `FakeFio` (scenario-driven, returns fio 3.x JSON, logs and clat percentile ladders). Scenario knobs: per-rung rate; cache `(C, F, R)` with hit/miss latency modes; plateau; burst-then-throttle; flat cap; slow disk; fresh-block penalty; byte or IOPS metering; a moving ceiling (rate multiplier by clock); noise; a crash at stage N. *Oracle:* `test_arc_03_fakefio_scenarios` (each knob observable in the emitted JSON through the production parser).
* **IOB-ARC-04** Determinism: with the same Host responses, clock and token, a run produces byte-identical `result.json`. *Oracle:* `test_arc_04_deterministic_result` (run twice, compare bytes; a Host whose `token()` varies yields a different `run.id` and nothing else).
* **IOB-ARC-05** Every device-touching action is built by a typed builder in `devio.py`: fio jobs, `blkdiscard`, the fill, `blkid -p`, `wipefs -n`, `swapon --show`. There is no builder (and a static test forbids any argv) for `mkfs*`, `wipefs` without `-n`, `mount`, `umount`, `dd of=`, `systemctl set-property` or `daemon-reload`. Builders take the verified `(devno, partuuid, size)` and the planned extent; `Host.write_range/discard_range` re-check the live values and the extent. *Oracle:* `test_arc_05_builders_only` (grep of every `Host.run` argv in a full FakeHost run against the allowlist), `test_arc_05_no_forbidden_argv_exists`.

## 2. Target guard (finding P-01, ruling R-6)

* **IOB-TGT-01** `iobench target init DEV` MUST refuse, exit 2, write nothing, and name the reason, when the device, any partition or holder of it, or its parent disk, is: mounted (compared in **both** `/proc/self/mountinfo` and `/proc/1/mountinfo`; the run is refused if the two differ in any entry touching the device, see IOB-SAF-09), swap-active (`/proc/swaps`), carries a filesystem, partition-table, LVM or RAID signature (`blkid -p`, `wipefs -n` non-empty) other than an iobench marker, or contains `/` or `/boot`. *Oracle:* `test_tgt_01_refuses_{mounted,mounted_other_namespace,swap,signature,root,parent_of_mounted,holder}` (one controlled-wrong case each; each asserts zero bytes written to the FakeHost device and a message naming the condition).
* **IOB-TGT-02** The signature override is `--i-own-this-device` **plus** `--expect-size BYTES` equal to the actual size **plus** `--expect-partuuid ID` equal to the actual PARTUUID (or `--expect-serial` when the device reports a non-empty serial). A device with neither a PARTUUID nor a serial is **refused outright** (size alone is not identity). Mounted, swap-active and root refusals are never overridable. *Oracle:* `test_tgt_02_override_matrix` (flag subsets x match/mismatch x each refusal kind) and `test_tgt_02_size_only_device_refused`.
* **IOB-TGT-03** The marker is the last 1 MiB of the device. No writer (fio, discard, fill) may touch the last 1 MiB. iobench writes no filesystem anywhere (IOB-ARC-05); the signature set of a target is exactly {iobench marker}. *Oracle:* `test_tgt_03_reserved_tail_one_per_writer_type` (separate controlled-wrong tests: a fio job, a discard range, a fill job and a marker rewrite whose extent overlaps the tail each raise from the FakeHost device model).
* **IOB-TGT-04** Before **every stage** that writes or discards the device, iobench verifies the marker exists and its size and PARTUUID still match the live device, and that the signature set is exactly {iobench marker}. This applies to fio jobs, discards and the fill alike, in every profile, **including `--installer`** (which only relaxes the live-host refusals of IOB-SAF-05/06, never any TGT requirement). *Oracle:* `test_tgt_04_{no_marker,size_changed,partuuid_changed,foreign_signature_appears,installer_flag_does_not_bypass}_refused`.
* **IOB-TGT-05** A work dir MUST be new/empty or contain only `.iobench-workdir`; MUST be on an existing filesystem whose type is not `tmpfs` or `ramfs` (and not `overlay` unless `--installer`); MUST have free space >= max(4 GiB, 3 x the planned volume); files are created with explicit byte caps and removed at the end. A work dir on the root filesystem is allowed only with `--confirm-quiet-window` (the live-host use case). The work dir's parent disk SHOULD be the target's parent disk; if it differs, every S3-S5 proposal carries reason `cross_disk`. *Oracle:* `test_tgt_05_workdir_rules` (tmpfs refused, overlay refused/allowed, non-empty refused, cross-disk flagged).

## 3. Time-series reduction (findings B-01, B-02, B-03)

* **IOB-SER-01** `reduce_series(points)` takes `(t_seconds, rate)` pairs and MUST raise `SeriesError` for: fewer than 8 points; NaN, infinity or negative values; timestamps that are not strictly increasing; a zero steady value. Every planned job logs at <= 2 s resolution so that a 30 s run yields >= 12 points after the 5 s warm-up discard. *Oracle:* `test_ser_01_refusals` (NaN at index 3, 20 samples all at t=0, duplicates, negative, 7 points, all-zero) and `test_ser_01_planned_point_counts` (every planned fio job's `runtime / log_avg_msec - warmup` >= 8).
* **IOB-SER-02** Steady = median of the last 25 % (min 3 points). The knee is the first index after which the rolling median (centered, window 5, edges shrink) stays within +-15 % of steady; `None` if no such index exists in the first 90 % of the series. *Oracle:* `test_ser_02_noisy_tail_knee` with the numerically verified fixture: 10 samples at 100000 followed by 30 samples alternating 18000/22000 (dt = 5 s; tail CV = 0.10, well under the 0.20 `unsteady` threshold) MUST give `state = steady`, `steady = 20000`, `knee_s = 50`, and `burst_units` within [3.6e6, 4.4e6] (clean-step reference 4.0e6; the noise above steady MUST NOT be added: burst <= 1.1 x the clean-step burst). `test_ser_02_clean_step`; `test_ser_02_noise_at_threshold` places a second fixture at tail CV 0.25 and asserts `unsteady`.
* **IOB-SER-03** `state` is `steady` (tail flat, knee found), `flat` (flat from the start), `unsteady` (tail CV > 0.20 or tail trend > 0.20 of steady). `steady_certified` is true for `steady` and `flat`. Each reading carries `observation_s` (series duration). A `flat` reading says "no burst observed in `observation_s`" and never "no burst exists"; the result limits (IOB-RES-04) state the longest observation. The old name `passed_knee` MUST NOT exist. *Oracle:* `test_ser_03_states`; `test_ser_03_no_passed_knee_name` (grep); `test_ser_03_flat_reason_text`.
* **IOB-SER-04** `rate_limited` is true when the coefficient of variation over a window of `min(60 s, series length)` is < 1 % for some window, and the series is at least 60 s long; for shorter series the field is `not_assessed`. Informational only. *Oracle:* `test_ser_04_flat_line` (4096 MiB/s +-1 over 600 s -> true; the noisy 21.6k-IOPS series -> false; a 40 s flat series -> `not_assessed`).
* **IOB-SER-05** `burst_units` = integral of (smoothed rate - steady) over samples before the knee. `mean_overstates(reading, ratio)` takes the ratio as a required argument. *Oracle:* `test_ser_05_burst_units` (clean step 100000 -> 20000: exactly 4.0e6 when dt = 5 s and 10 samples) and `test_ser_05_ratio_required`.

## 4. Proof model: statuses, reasons, exit codes (rulings R-1, R-9)

* **IOB-STA-01** Every proposed value (each cap key, each model key, each QoS key, each writeback value) carries its own `status`: `proven`, `unproven` (with `reasons[]` from the closed vocabulary below) or `absent` (with one `reason`). A value never has a status that is stronger than its weakest basis.
* **IOB-STA-02** Closed vocabularies:
  * `unproven` reasons: `cache_model_undetermined`, `cache_residual_possible`, `no_cliff_hit_like_mode`, `device_smaller_than_cache_margin`, `ceiling_moved`, `budget_limited`, `short_observation`, `quick_profile`, `worst_from_single_run`, `metering_undetermined`, `operator_override`, `shape_fit_poor`, `noise_floor_high`, `drain_below_ladder`, `formula_only`, `target_missed`, `cross_disk`, `identity_weak`.
  * `absent` reasons: `stage_skipped`, `stage_failed`, `basis_unsteady`, `basis_missing`, `bps_basis_missing`, `no_calibration`.
  * `cache_state` values: `plateau_cache_free`, `cliff_modeled`, `no_cliff_miss_like`, `no_cliff_hit_like`, `undetermined`.
  * `metering` values: `byte`, `iops`, `mixed`, `undetermined`.
  * `fit` values: `good`, `poor`, `insufficient`.
  * `limits[]` values: `quick-profile`, `limited-by-budget`, `short-observation`, `single-run`, `stage_skipped:<Sn>`, `bucket_exclusion_s<=<N>`.
* **IOB-STA-03** A cap, model or QoS value whose basis may still contain cache hits is **never** `proven`, whatever a single fit says; `proven` for a cache-dependent basis requires the two independent estimators of section 6 to agree, or a proven plateau, or a proven miss-like unimodal result. *Oracle:* `test_sta_03_cache_dependent_never_proven_without_agreement` (controlled-wrong: perfect E1 fit, E2 missing -> `unproven`).
* **IOB-STA-04** The wizard/installer MAY apply an `unproven` value only after an explicit operator accept that displays its reasons; it MUST NOT apply an `absent` value. `iobench` itself applies nothing. *Oracle:* contract test `test_sta_04_show_marks_apply_requirements` (the `show --proposals` output contains `apply: requires_accept` for each unproven value, `apply: not_possible` for absent).
* **IOB-STA-05 (status collapse audit, ruling R-1)** `check` states and proposal statuses are each reachable only from the conditions in the tables below; `test_sta_05_status_collapse_audit` constructs one scenario per row and asserts the status in both directions (the row's condition yields the status; a condition from another row does not). A row missing from this table cannot be tested, so the table is the contract.

| Proposal status | Reached only when |
|---|---|
| `proven` | the value's basis is steady (`steady`/`flat`) AND not cache-dependent or the cache state is `plateau_cache_free`/`cliff_modeled`/`no_cliff_miss_like` AND no listed unproven reason applies |
| `unproven: cache_model_undetermined` | cache state `undetermined` (non-monotone, too few post-cliff points, E1 and E2 disagree, overlapping modes, unimodal-not-classifiable) |
| `unproven: no_cliff_hit_like_mode` | no cliff observed AND the latency histogram is unimodal hit-like |
| `unproven: cache_residual_possible` | a figure is shown from the measured largest rung while the estimators are not both available |
| `unproven: device_smaller_than_cache_margin` | cliff observed, largest rung < 1.2 x cliff |
| `unproven: ceiling_moved` | a reference cell moved > 15 % across the comparison |
| `unproven: budget_limited` / `quick_profile` / `short_observation` | the profile or time budget truncated the ladder / profile is `quick` / the longest observation at the basis rung is below the stated threshold |
| `unproven: worst_from_single_run` | worst/best come from this run only (no ceiling history) |
| `unproven: metering_undetermined`/`shape_fit_poor`/`operator_override` | S6 tests did not both pass / linear fit poor / operator typed `--metering` |
| `unproven: noise_floor_high`/`target_missed`/`drain_below_ladder`/`formula_only`/`cross_disk` | sweep noise > 4 / R4 failed / drain rate disagrees with the ladder / sweep skipped / work dir on another disk |
| `absent: stage_skipped`/`stage_failed` | the producing stage was skipped (budget, refusal) / failed |
| `absent: basis_unsteady` | the basis rung stayed `unsteady` after extension |
| `absent: basis_missing`/`bps_basis_missing` | no rung/cell exists for the basis / no sequential 1 MiB cell |

| `check` state (exit) | Reached only when |
|---|---|
| `current` (0) | identity matches AND every requested proposal is `proven` AND the file is readable and a supported schema |
| `current_unproven` (7) | identity matches AND at least one requested proposal is `unproven` or `absent` (the JSON lists them; exit 7 is never raised for a group the caller did not request) |
| `stale` (3) | the disk identity (devno, partuuid, size, and any non-null model/serial/wwn) differs from the calibration's |
| `absent` (4) | no `result.json` (or, with `--calibration`, no `calibration.json`) |
| exit 5, `reasons[0]` in `corrupt|schema_unsupported|unreadable|calibration_missing` | truncated/invalid JSON / unknown major schema / OS error (EACCES, EIO) / the result's `calibration_id` has no matching calibration file |
| `aborted_only` (6) | the newest record is `aborted` or `failed` and no good record exists |
| `current` (0) or `current_unproven` (7) with warning `last_run_aborted` | a good record exists and a newer aborted/failed record also exists |

`check` always prints `limits[]`, `warnings[]` (closed: `scheduler_changed`, `ram_changed`, `kernel_changed`, `root_source_changed`, `workdir_fs_changed`, `older_than_180_days`, `recalibrate_recommended`, `last_run_aborted`) and `identity_strength` (`strong` if a serial or wwn is recorded, else `weak`; `weak` adds `identity_weak` to every proposal's unproven reasons only when the caller passes `--require-strong-identity`).

## 5. Calibration

* **IOB-CAL-01 (rung plan)** `usable = size - reserved tail`. `WS_max = min(0.85 x usable, R_est x fill_budget_s)` where `fill_budget_s` = 12 % of the calibration budget. Rungs: 1 GiB, doubling while < `WS_max`, then `WS_max` itself. After the first rung below the cliff threshold, up to four log-equispaced **densification rungs** between the last good rung and `WS_max` are added (budget permitting). The largest rung is never above `WS_max`. *Oracle:* `test_cal_01_rung_plan` asserts the exact list for the r1002 geometry (size 435853320192, `R_est` 1.36 GB/s, 3600 s budget): `[1,2,4,8,16,32,64,128,256,345.0] GiB` (WS_max = 0.85 x 405.9 GiB = 345.0 GiB) plus, for a cliff scenario at 270 GiB, four densification rungs log-equispaced between 256 and 345 GiB; and a slow-disk geometry where `R_est x fill_budget_s` is the bound. Test calls the production planner; the replay tests run through it.
* **IOB-CAL-02 (fill, confinement)** The fill writes `[0, WS_max)` sequentially (1 MiB, QD16) once; the written extent is **the bytes fio reports written**, so an interrupted fill (signal, budget skip, provider slowdown) records the shorter extent and `WS_max` shrinks to it. Every read job's `offset + size` MUST lie within the recorded extent (argv-level confinement). *Oracle:* `test_cal_02_confinement_argv` (controlled-wrong: shifting a read job beyond the extent raises before launching fio); `test_cal_02_interrupted_fill_records_actual_bytes` (fill cut at 40 %: extent [0, 0.4 WS_max), later rungs larger than that are not planned, status `unproven: budget_limited`).
* **IOB-CAL-03 (rung measurement)** Each rung runs random 4 KiB read and write at QD32 for 30 s (first 5 s discarded, 2 s log); the largest rung also runs QD1 and QD8. Rungs go through `reduce_series`; the record carries `steady_iops`, `mean_iops`, `state`, `observation_s`, `rate_limited`, latency p50/p90/p95/p99/p99.9 and a series downsampled to <= 120 points. An `unsteady` rung is extended up to 5 min and, if still `unsteady`, kept as `unsteady` and gives `absent: basis_unsteady` for any proposal that needs it (no fallback to a smaller rung). *Oracle:* `test_cal_03_rung_record_and_extension`.
* **IOB-CAL-04 (cliff, classes, no-cliff outcomes)** `cached_ref` = median steady IOPS of the first three rungs. The cliff rung is the first rung with steady < 0.75 x `cached_ref` such that no later rung recovers to >= 0.75 x `cached_ref`; `cliff_bytes` is its size, else null. Rungs below the cliff are class `cached_tier`, at or after it `post_cliff`. With `cliff_bytes == null` the class of every rung is `no_cliff` and section 6 decides the cache state (a valid outcome, ruling R-4). *Oracle:* `test_cal_04_cliff_detection` on: cliff at 270 GiB; no cliff; cliff only at the last rung; noisy small rungs; plus the **r1002 recorded replay** (cliff between 250 and 300 GiB).
* **IOB-CAL-05 (observation length)** `standard` adds one 240 s steady run (random write QD8, largest rung) and one 120 s run (random read); `thorough` replaces them with a 900 s write run and a 240 s read run. Every result records the longest `observation_s` at the basis rung and `limits += bucket_exclusion_s<=<N>`; if `N` < 120, `short_observation` applies to the cap values. *Oracle:* `test_cal_05_observation_limits`.
* **IOB-CAL-06 (cap basis)** Per direction the cap basis is the largest-rung steady IOPS, adjusted per section 6 (`min(measured_largest, R)` when proven); a `cached_tier` figure and an `unsteady` rung are never a basis. BPS keys: the basis is the steady bytes/s of the sequential 1 MiB QD16 cell of S6 at the cap-basis working set, multiplied by `cache_ratio = R / measured_largest` (<= 1) of the **same direction** when the cache state is proven, else by 1 with `unproven: cache_residual_possible`; if no such cell exists the BPS caps are `absent: bps_basis_missing`. A flat-line (`rate_limited`) sequential cell is itself a proven upper bound. *Oracle:* `test_cal_06_cap_basis`, `test_cal_06_bps_basis_rules` (four cases: proven ratio, unproven ratio, flat-line cap, missing cell).
* **IOB-CAL-07 (fresh versus rewrite, writes only, ruling R-5)** On an iobench-marked target that supports discard, the calibration discards a **fresh extent** located above `WS_max` and below the reserved tail (size = `min(usable - WS_max - reserved, 8 GiB)`), measures random 4 KiB QD32 writes to it for 30 s (`fresh`), and to a written extent (`rewrite`); `fresh_penalty = rewrite/fresh`. The fresh extent is re-discarded before each fresh cell and removed from the written-extent map after use. Fresh applies to writes only. If discard or space is unavailable: `fresh_write: not_measured` with the reason. The write-IOPS cap basis is `min(rewrite, fresh)` when both are measured. *Oracle:* `test_cal_07_fresh_penalty` (3596 vs 71584 -> 19.9; unsupported -> `not_measured`); `test_cal_07_fresh_extent_is_disjoint` (no overlap with `[0, WS_max)` nor the tail, re-discard between cells, extent map updated); `test_cal_07_write_cap_uses_min`.
* **IOB-CAL-08** `QD90` is computed per direction as the smallest tested QD whose steady >= 0.9 x the maximum over the tested depths at the largest rung. Depths between tested values are untested and the record says so. *Oracle:* `test_cal_08_qd90` (r1002: 23.7k at QD8, 23.9k at QD32 -> 8).
* **IOB-CAL-09 (artefact)** `calibration.json` is written atomically when the calibration status is `complete`: all stages the profile **requires** (S0, S1, fill, ladder) are `ok`; optional stages (S6, S7, fresh, CDF runs) may be `skipped: budget` and are recorded, with dependent proposals `absent: stage_skipped` and `limits += stage_skipped:<Sn>`. A ladder truncated before any cliff is `complete` with cache state from section 6. `aborted`/`failed` writes `calibration.last-failed.json` and never replaces a good one. *Oracle:* `test_cal_09_complete_definition`, `test_cal_09_atomic_no_replace`.

## 6. Cache state: two independent estimators (ruling R-2)

* **IOB-CCH-01 (inputs)** Per direction: the ladder steady IOPS per rung; the clat percentile ladder `1,5,10,20,30,40,50,60,70,80,85,90,95,97,98,99,99.5,99.9,99.99` measured at **QD1** for 60 s on the smallest rung (`hit_ref_us` = its p50) and on each of the largest three rungs. *Oracle:* `test_cch_01_inputs_planned`.
* **IOB-CCH-02 (E1, hit-ratio fit)** For post-cliff rungs (`ws > C`) the model is `1/iops = A + B x (1/ws)` with `A = 1/R`, `B = C (1/F - 1/R)`, `F` = median steady of the `cached_tier` rungs; so `R = 1/A`, `C = B / (1/F - A)`. E1 is `good` iff: >= 4 post-cliff rungs; rungs are monotone non-increasing within +5 % per step; `A > 0`, `B < 0`; `0 < C < smallest post-cliff rung`; `R <= min(post-cliff steady)`; `r2 >= 0.97`; and every leave-one-out prediction is within 15 % of the omitted rung. Otherwise `poor` (fit failed a rule) or `insufficient` (< 4 rungs). The function MUST NOT raise on non-monotone, equal or degenerate input; it returns `poor`/`insufficient` with the failed rule named. *Oracle:* `test_cch_02_*` for the fixtures of IOB-CCH-06.
* **IOB-CCH-03 (E2, latency-mode split)** From a QD1 percentile ladder: the largest log-ratio gap between consecutive percentile latencies defines a candidate split. The distribution is **bimodal** iff the gap ratio >= 2.5, each side contains >= 2 ladder points and each side's own spread (max/min) <= 2.0; **overlapping** if the largest gap ratio is in [1.5, 2.5) or a side's spread > 2.0 with a gap >= 1.5; **unimodal** otherwise. For bimodal: `h2` = the percentile position of the split (fraction of IOs in the lower mode), `t_hit`, `t_miss` = the medians of the two sides; `R_E2 = iops_largest(QD32) x (1 - h2)`; require `h2` in [0.05, 0.95]. A unimodal histogram is **hit-like** if its p50 <= 1.5 x `hit_ref_us`, **miss-like** if >= 2.5 x `hit_ref_us` or the series is `rate_limited`, else indeterminate. *Oracle:* `test_cch_03_*`.
* **IOB-CCH-04 (cache state)** `classify_cache` returns `cache_state` and the evidence:

| Situation | `cache_state` | Status of cache-dependent values |
|---|---|---|
| no cliff; largest rung `rate_limited` or unimodal miss-like | `no_cliff_miss_like` | `proven` (nothing cached or cap-bound) |
| no cliff; unimodal hit-like (and not rate-limited) | `no_cliff_hit_like` | `unproven: no_cliff_hit_like_mode` |
| no cliff; any other (bimodal, overlapping, indeterminate) | `undetermined` | `unproven: cache_model_undetermined` |
| cliff; >= 3 post-cliff rungs within 10 % of each other (true plateau) AND largest rung unimodal miss-like | `plateau_cache_free` | `proven`; **not** labelled poor; value = median of the plateau rungs |
| cliff; E1 `good` AND E2 bimodal AND `abs(h1 - h2) <= 0.15` (`h1 = C/ws_largest`) AND `R_E1/R_E2` in [0.7, 1.43] | `cliff_modeled` | `proven`; `R = min(R_E1, R_E2)` |
| cliff; anything else (E1 poor/insufficient, E2 overlapping/unimodal/missing, estimators disagree, non-monotone) | `undetermined` | `unproven: cache_model_undetermined` (E1 and E2 values kept in the evidence) |
| cliff, largest rung < 1.2 x `cliff_bytes` | (any of the above) plus | `unproven: device_smaller_than_cache_margin` |

* **IOB-CCH-05 (values)** Proven: cap value `min(measured_largest, R)` (plateau: the plateau median). Unproven: `min(measured_largest, R_E1 if E1 produced one)`, shown with its reasons. A cap value never exceeds `measured_largest`. *Oracle:* `test_cch_05_value_never_exceeds_measured` (including a scenario in which E1 reports R = 3 x measured and E2 disagrees: value <= measured, status `unproven`).
* **IOB-CCH-06 (controlled-wrong and degenerate fixtures; each is a named test and a FakeFio scenario)**
  1. `non_monotone` (a post-cliff rung 20 % above its predecessor) -> E1 `poor`, `undetermined`.
  2. `C_ge_smallest_rung` (fit gives C >= smallest post-cliff rung) -> `poor`, no exception.
  3. `two_rungs` and `three_rungs_noise` (3 monotone-looking noisy points) -> `insufficient`/`poor`; never `good`.
  4. `true_plateau` (flat within 5 %, unimodal miss-like) -> `plateau_cache_free`, `proven`, NOT poor.
  5. `no_cliff_miss_like`, `no_cliff_hit_like`, `no_cliff_rate_limited`.
  6. `bimodal_agree` (synthetic C=265 GiB, F=92k, R=9.8k from the r1002 fit; rungs 277/300/324/345 GiB; QD1 CDF bimodal with t_hit 110 us, t_miss 600 us) -> `cliff_modeled`, `R` in [8.5k, 11.5k].
  7. `bimodal_disagree` (E2 h2 differs by > 0.15) -> `undetermined`.
  8. `overlapping_modes` (t_miss/t_hit = 1.8) -> `undetermined`.
  9. `R_wrong_3x` (see CCH-05).
  10. `recorded_r1002` (real recorded rungs <= usable: 1, 16, 100, 150, 200, 250, 300 GiB read; 350/400 are beyond `0.85 x usable` and excluded): only one post-cliff rung -> E1 `insufficient` -> `undetermined`, **an honest outcome that the test asserts**.
  Each fixture is stamped `synthetic: true` or `recorded: <tarball sha256>` in its header.

## 7. Slow disks, budgets and stage allotments

* **IOB-SLO-01** S1 (rate probe) is a 20 s **direct sequential 1 MiB write to the first 1 GiB of the target** (the device the fill will use), sampled each second; `R_est` = median of the last 10 samples. The work dir's buffered rate is S3's measurement, not `R_est`. *Oracle:* `test_slo_01_rate_probe` (fake series 1.2..1.4 GB/s -> `R_est` = the stated median).
* **IOB-SLO-02** No stage writes more than `1.2 x R_est x stage_seconds` bytes, enforced by fio's `--io_size` (fio stops at the earlier of runtime and io_size) and by the free-space bound. A stage truncated by its byte cap yields a short series; if it has < 8 points the stage status is `failed: insufficient_points` and dependents are `absent: stage_failed` (never a guessed value). *Oracle:* `test_slo_02_volume_bound` (14 MB/s scenario; also the **3 x `R_est` scenario**: actual rate triples mid-run, bytes written still <= the bound, truncated stage reported).
* **IOB-SLO-03** If `R_est < 5 MB/s`, or the fill does not fit `fill_budget_s`, the calibration runs time-bounded from the measured cap: `WS_max` shrinks by IOB-CAL-01, `limits += limited-by-budget`, cache-dependent values `unproven: budget_limited`. The sweep is skipped only if the quiet gap cannot drain the dirty pool within its bound (then writeback `unproven: formula_only`). *Oracle:* `test_slo_03_slow_disk_time_bounded`.
* **IOB-SLO-04** A hard wall-clock budget bounds the run. Stages run in priority order S0, S1, fill, ladder, CDF runs, reference cells, S3, S4, S5, S6, S7 (calibration: S0, S1, fill, ladder, CDFs, fresh/rewrite, refs, observation, S6, S7); a stage that cannot fit its minimum is skipped and recorded `skipped: budget`. *Oracle:* `test_slo_04_priority_and_skip_record`.
* **IOB-SLO-05 (allotment table, finding S-8)** Section 15 contains the per-stage allotment. A planner function `plan(profile, geometry)` returns the allotment; *Oracle:* `test_slo_05_standard_fits_on_r1002_geometry` asserts `sum(allotments) <= budget` for `standard` (3600 s) and `thorough` (4500 s) on the r1002 geometry with S6 and S7 included, and `test_slo_05_matrix_worst_case_fits` asserts the matrix worst case <= 900 s and the knob plan typical <= 240 s, worst <= 480 s.

## 8. Reference cells and the moving ceiling (ruling R-8)

* **IOB-REF-01** The reference cell per direction is random 4 KiB QD8 over the first 1 GiB of the written extent, 15 s (3 s discarded). It runs before and after every comparison group (the ladder, S6, fresh-vs-rewrite) and comparisons are measured interleaved as `[ref, A, B, ref]`. `ceiling_moved` is true iff `abs(ref_after/ref_before - 1) > 0.15`. *Oracle:* `test_ref_01_protocol` (the plan contains the pairs; a scenario with a rate multiplier 0.5 mid-run -> true; static scenario -> false).
* **IOB-REF-02** A comparison whose reference moved is `unproven: ceiling_moved`; both values are kept in the evidence. It is **not** a plausibility failure. The move is a finding: a ceilings.jsonl observation with `note: "reference_shift"` is appended (IOB-CEI-01). *Oracle:* `test_ref_02_moved_is_finding_not_failure` (the passing case is the healthy "moving cap" scenario, built first: a refusal needs its legitimate state constructed before it ships).

## 9. Drain rate, sweep, read cost

* **IOB-DRN-01** `R_drain` = median of the last 25 % of 1 s samples of device write bytes/s from `/proc/diskstats` while the reference load runs 60-90 s in the work dir: a buffered writer creating new 1 GiB files (removed behind itself, volume-bounded) plus a 4 KiB random-write job with `fsync=16`, 2 jobs. `rates_consistent` iff `R_drain` is within 1.3x of the ladder's `min(rewrite, fresh)`; else `drain_below_ladder` (`unproven`) or `drain_above_ladder` (`unproven: cache_residual_possible`). *Oracle:* `test_drn_01_drain_rate_and_consistency`.
* **IOB-SWP-01** The default sweep has **6 cells**: REF, `dirty_bytes/2`, `nr_requests x2`, `max_sectors_kb` one step up, `max_sectors_kb` one step down, and REF again last; cell length 60 s (60-90 s allowed); the quiet gap ends when dirty+writeback < 4 MiB for 5 s or after 60 s. `--full-sweep` (thorough) adds `dirty_bytes x2` and `nr_requests /2` (8 cells). Typical planned time (cell 60 s + gap 20 s) 480 s; worst case (gap 60 s) 720 s. *Oracle:* `test_swp_01_plan_counts_and_times` asserts 6 cells, 8 with `--full-sweep`, typical 480 s, worst 720 s.
* **IOB-SWP-02** The probe appends 200 B and calls `fdatasync` every 0.5 s on the work dir's filesystem and records per-op latency; it runs as a transient unit in a slice without IO limits and with `RuntimeMaxSec`. *Oracle:* `test_swp_02_probe_unit_properties` (unit text contains `RuntimeMaxSec=`, the slice name, no `IO*Max=`; probe interval 0.5 +-0.05 s in the fake clock).
* **IOB-SWP-03** Noise floor = max/min of REF's two probe p99 values. A neighbour replaces REF only if its probe p99 beats REF's by more than the noise ratio in its cell **and** in a repeat, and the gate-like load's fsync p99 and iops did not regress by more than the noise ratio; otherwise REF stands. Noise > 4 -> `unproven: noise_floor_high`. *Oracle:* `test_swp_03_decision_rule` (lucky single cell loses; consistent 5x winner wins; noise 5 -> unproven).
* **IOB-SWP-04** The sweep sets only `vm.dirty_bytes`, `vm.dirty_background_bytes`, `queue/nr_requests`, `queue/max_sectors_kb` of the derived disk, through the restore journal. *Oracle:* `test_swp_04_only_allowed_knobs` (FakeHost write log equals the allowlist).
* **IOB-RDC-01** For each candidate `max_sectors_kb` and for `max_hw_sectors_kb`: direct 1 MiB sequential reads at QD4, 10 s, over the written extent; `read_ratio` = candidate/`max_hw` bytes/s; plus one buffered pair (64 vs 256). Three candidates take 30 s + the buffered pair 20 s. *Oracle:* `test_rdc_01_read_ratio` (replay: 1.3 GB/s vs 0.27 GB/s -> 0.21; plan time 50 s).

## 10. Cost shape and metering (ruling R-9)

* **IOB-SHP-01 (grid)** Rewrite cells: sizes {4 KiB, 16 KiB, 64 KiB, 256 KiB, 1 MiB} x {sequential, random} x {read, write} = 20 cells at QD32 on the cap-basis working set, 30 s each, steady tail via `reduce_series`. Fresh cells (writes only): random 4 KiB and sequential 1 MiB on the fresh extent = 2 cells. **Total 22 cells.** There is no `read x fresh` cell. The execution order is a fixed seeded shuffle with anchors (random 4 KiB and sequential 1 MiB, each direction) repeated at start, middle and end; the anchor spread estimates drift (`ceiling_moved` if > 15 %). Each cell: `{dir, pattern, bs, block_state, offered_bps, steady_bps, steady_iops, lat_us{p50,p90,p99}, cv, flat_line}`. *Oracle:* `test_shp_01_grid_plan` asserts 22 cells, no read-fresh cell, anchors present, no cell extent overlapping the fresh extent except fresh cells, and the fixed order for a fixed seed.
* **IOB-SHP-02 (fit)** Per `dir x pattern x block_state`: `1/steady_iops = c_io + bs x c_byte` by linear regression over the sizes; `r2`, `residual_max`; `linear_model_fit` is `good` iff `r2 >= 0.9` and `residual_max <= 0.25`. *Oracle:* `test_shp_02_fit` (synthetic c_io = 80 us, c_byte = 0.25 ns recovered within 2 %; noise-only -> poor).
* **IOB-SHP-03 (metering, both tests)** `T_size`: for byte metering, CV of `steady_bps` over the sizes <= 0.15 AND CV of `steady_iops` >= 0.40; for iops metering, the converse. `T_pattern`: at >= 4 of the 5 sizes `abs(seq/rand - 1) <= 0.20` in the metered quantity. `metering = byte` only if `T_size` (byte form) AND `T_pattern` hold; `iops` likewise; `mixed` if the fit is `good`, `c_io > 0`, `c_byte > 0` and `T_pattern` holds; otherwise `undetermined` (also when a reference moved: `unproven: ceiling_moved`). `fresh_penalty` is a separate field (IOB-CAL-07) and never changes `metering`. A typed override `--metering byte|iops|mixed` is stored as `override` and makes dependent values `unproven: operator_override`. *Oracle:* `test_shp_03_metering` (one scenario per class; **controlled-wrong**: flat bytes across sizes but seq != rand -> `undetermined`, not `byte`; seq == rand but not flat -> `undetermined`; a byte-metered disk with a fresh penalty stays `byte`).
* **IOB-SHP-04 (model keys)** Model keys derive only when the fit is `good` for that direction: `*bps` and `*seqiops/*randiops` per section 12 (R6). When `poor`: `{"status": "absent", "reason": "stage_failed"}`-style null with `unproven: shape_fit_poor` for the dependent QoS; never a guess. When `metering == undetermined` and the fit is `good`: the keys are computed from the fit and are `unproven: metering_undetermined`. *Oracle:* `test_shp_04_model_keys`.

## 11. Latency and ceiling history

* **IOB-LAT-01** S7: at 10, 30, 60, 90 % of the cap-basis ceiling (random 4 KiB, `--rate_iops`), 20 s each per direction, record p50/p90/p95/p99 and achieved rate; a point whose achieved rate < 90 % of offered is `not_reached`. `light_p90`/`light_p95` are the larger of the 10 % and 30 % points. *Oracle:* `test_lat_01_curve_and_light_points`.
* **IOB-LAT-02** The latency knee is the first load point whose p99 >= 2 x the 10 % point's p99, else null. *Oracle:* `test_lat_02_knee` uses a **synthetic** fixture (declared `synthetic: true`; derived from the IO-BENCH-01 background-QD sweep shape, since that data was not recorded at rate-limited load points).
* **IOB-CEI-01** `ceilings.jsonl`: append-only; first line is a header `{header, schema, identity_digest}`; each observation `{ts, dir, workload, plateau_bps, duration_s, source, note?}` with `workload` in `seq1m|rand4k`, `source` in `benchmark|governor|probe`, `note` optional (`reference_shift`). Appends are atomic (`Host.append_line`, single write + fsync). *Oracle:* `test_cei_01_append_only_atomic`.
* **IOB-CEI-02** Refused: `plateau_bps <= 0`, `duration_s < 60`, non-finite values, an unknown `source` or `workload`. (The reference-shift note is exempt from the 60 s rule: duration 15 s.) *Oracle:* `test_cei_02_refusals`.
* **IOB-CEI-03** `summary` returns worst, median, best, n per `(dir, workload)` over a window; `history` is `recorded` (n >= 1) or `single-run` (no records; the caller uses the run's own windows). A history whose identity digest differs from the live disk is ignored with a warning. *Oracle:* `test_cei_03_summary_and_identity`.
* **IOB-CEI-04** `recalibrate_recommended` (a warning, never an invalidation): the median plateau of the last 12 h differs from the calibration's by > 25 % in two consecutive 12 h windows. Nothing populates the history automatically until the probe/governor exists (D-011); until then the rule can only fire from manually added or benchmark-added observations, and `check` says so (`history: single-run`). *Oracle:* `test_cei_04_recalibrate_rule`.
* **IOB-CEI-05** A complete run appends one `benchmark` observation per direction and workload for each sustained-plateau window (>= 60 s, state `steady`/`flat`) it measured at the cap-basis rung. *Oracle:* `test_cei_05_run_appends_plateaus`.

## 12. Matrix and derivation

* **IOB-MAT-01** `matrix` refuses (exit 2, "no current calibration") unless `calibration.json` exists, parses, has a supported schema and its identity matches. *Oracle:* `test_mat_01_requires_current_calibration` (absent, stale, corrupt).
* **IOB-MAT-02** Default plan = S0, S3, 6-cell sweep (IOB-SWP-01), S5: typical 650 s, worst case 890 s. `--knob NAME --candidates a,b[,c] --repeat` = interleaved A B A B cells of 45 s with a gap of <= 15 s typical (<= 60 s bound), no S3: typical 220 s, worst 420 s. *Oracle:* `test_mat_02_plan_budgets` asserts these four numbers.
* **IOB-MAT-03** The matrix uses only the calibrated written extent and the calibrated figures; it MUST NOT recompute caps or the model. *Oracle:* `test_mat_03_reuses_calibration` (the result's caps/model equal the calibration's, `calibration_id` set).
* **IOB-MAT-04** Recalibration triggers: identity change (`check` exit 3) and `recalibrate_recommended`. A matrix on a stale calibration refuses; on `recalibrate_recommended` it warns and proceeds. *Oracle:* `test_mat_04_recalibration_triggers`.

Proposals live in `proposals.*`; each value is `{value, status, reasons|reason, rule, inputs, basis, flags[]}` with `basis` in `measured`, `confirmed-by-sweep`, `ratio-inferred`, `formula-only`, `single-run`. A value is a number or `"kernel"`.

* **IOB-DER-01 (R1)** Order: R2 and R3 first (R1 consumes them). `inflight = nr_requests x max_sectors_kb x 1024`. `dirty_bytes` = largest power-of-two MiB <= `W x R_drain - inflight`, clamped to `[4 MiB, 25 % of T_eff]`, where `T_eff` = the kernel's effective threshold computed from `vm.dirty_ratio x available memory` using the journal's **original** values (a `vm.dirty_bytes` already applied by host-setup is ignored: no ratchet); both `T_eff` and the applied value are recorded in `inputs`. `"kernel"` if the unclamped value >= `T_eff`. If `inflight >= W x R_drain` the value is the 4 MiB floor with flag `inflight_exceeds_budget`. `dirty_background_bytes` (R1b) = `dirty_bytes / 4`. *Oracle:* `test_der_01_r1` incl. gstammtisch replay (`R_drain` 10.5 MB/s, q16, r64 -> 16 MiB / 4 MiB), r1002 replay -> `"kernel"`, the ratchet case (applied 16 MiB does not lower the next proposal), and the `inflight_exceeds_budget` case.
* **IOB-DER-02 (R2)** `nr_requests` = `2 x QD90(write, largest rung)` rounded to a power of two in `[8, 64]` (the tested depths are 1, 8, 32 (+64 in `thorough`), so 8, 16 or 64 can result); `"kernel"` if not below the current value. *Oracle:* `test_der_02_r2`.
* **IOB-DER-03 (R3, R4 normative)** `max_sectors_kb` candidate = the largest power-of-two KB <= `R_drain x 0.010 / 1024` (`t_req` = 10 ms, labelled `[I]`: a request should occupy the device <= 10 ms), clamped to `[32, max_hw_sectors_kb]`; then raised to the smallest candidate with `read_ratio >= F` unless the sweep shows that candidate's probe p99 > `P` (then flag `read_floor_violated`); `"kernel"` if >= `max_hw`. R4: the chosen triple's REF probe p99 must be <= `P`, else `unproven: target_missed` with the closest cell. *Oracle:* `test_der_03_r3_r4` (gstammtisch -> 64 with `read_floor_violated` at `read_ratio` 0.21; the 128 KB fallback; r1002 -> `"kernel"`).
* **IOB-DER-04 (R5 caps)** `caps.dev_aggregate.K = U x basis_K x min(1, worst/median)`, `K` in `RIOPS, WIOPS, RBPS, WBPS`; the basis per IOB-CAL-06/-07/-CCH-05 and `worst/median` from the ceiling summary for the same direction and workload (1.0 and `unproven: worst_from_single_run` when history is `single-run`). Using `worst` makes the cap bind when the provider drops to its worst plateau. No per-tier caps. *Oracle:* `test_der_04_r5` asserts four numbers for a fixture (e.g. basis 9400 read IOPS, U 0.70, worst/median 0.43 -> RIOPS 2829) and the absence of tier keys.
* **IOB-DER-05 (R6 model)** Basis = the **worst plateau** per direction (ceiling summary `worst` for `seq1m`, else this run's lowest steady window with `unproven: worst_from_single_run`). Per metering class: `byte`: `wbps = worst`, `wseqiops = wrandiops = wbps/4096` (and the read analogue); `iops`: the IOPS keys = worst IOPS plateau, `*bps` = IOPS x 1 MiB-class fit; `mixed`/`undetermined`: `*bps = min(1e9/c_byte, worst)` and `*iops = 1e6/c_io`, the latter `unproven: metering_undetermined` when undetermined. *Oracle:* `test_der_05_r6_model` (plateau history 6/14/18 MiB/s byte-metered -> `wbps = 6291456`, `wseqiops = 1536`; read not byte-metered -> `rseqiops` from the fit).
* **IOB-DER-06 (R6 QoS)** `min = 100`; `max = ceil(110 x max(best_r/worst_r, best_w/worst_w))` over directions that have history; `wpct = 90`, `wlat = 2 x light_p90` rounded **up to 5 ms** and clamped to [10, 50] ms; `rpct = 95`, `rlat = 2 x light_p95` rounded **up to 1 ms** and clamped to [2, 20] ms (values emitted in microseconds). Histogram source recorded: `governor` (a file via `--governor-histogram`) else `benchmark`. *Oracle:* `test_der_06_r6_qos` (18/6 MiB/s write and 1.4/1.3 GB/s read -> max 330; light p90 4.2 ms -> `wlat` 10 ms; 17 ms -> 35 ms; light p95 read 0.6 ms -> `rlat` 2 ms; 3.2 ms -> 7 ms).
* **IOB-DER-07** Every proposal has `rule` and `inputs` sufficient to recompute it (including `T_eff`). *Oracle:* `test_der_07_recomputable` recomputes every value from its `inputs`.
* **IOB-DER-08** No proposal derives from a figure that is `cached_tier`, `unsteady`, or from a stage whose status is not `ok`; the proposal becomes `absent` with the reason. *Oracle:* `test_der_08_no_derivation_from_bad_basis` (one controlled-wrong input per class).
* **IOB-DER-09** Fresh-block penalty is consumed: write-IOPS cap basis = `min(rewrite, fresh)`; `proposals.notes` includes `fresh_write_penalty` when `fresh_penalty >= 3`. *Oracle:* `test_der_09_fresh_consumed`.

## 13. Plausibility (runtime checks only)

`quality.checks[]` holds `{id, status: pass|fail|info|not_applicable, detail, degrades: [proposal groups]}`. A `fail` of a **data-integrity** check fails the whole run (PLA-14, -15, -16 surface as `run.status: failed` + `*.last-failed.json`, never as a stored `fail`). All other checks degrade only the groups they list, via proposal statuses (section 4). Each check has a controlled-wrong oracle; the passing "moving cap" and "healthy" scenarios are built first. `rotational` plays no role.

| Id | Check | Result |
|---|---|---|
| **IOB-PLA-01** | tail flat | the **largest planned rung** is `unsteady` after extension -> `fail`; no fallback to a smaller rung; degrades caps, model, QoS to `absent: basis_unsteady` |
| **IOB-PLA-02** | working-set rule | largest rung >= 1.2 x cliff -> `pass`; else `fail` with `unproven: device_smaller_than_cache_margin`; `limited-by-budget` yields `info`, never `pass` |
| **IOB-PLA-03** | cached-tier factor | `info` value `cached_tier_factor` = small-rung / largest-rung (report; a derivation using a small rung is a code defect caught by DER-08) |
| **IOB-PLA-04** | mean vs steady | `info` value `mean_overstates` (reported; the mean is never used) |
| **IOB-PLA-05** | flat-line | `info`: `rate_limited` (e.g. 4096 MiB/s sequential read) |
| **IOB-PLA-06** | Little's law | `iops x mean_clat / QD` outside [0.7, 1.3] for a rung -> `fail` (broken or faked fio) -> rung unusable (`absent`) |
| **IOB-PLA-07** | bytes consistency | random-4K bytes/s > sequential bytes/s of the same direction by > 5 % in an interleaved pair with unmoved reference -> `fail`; reference moved -> `info`, `unproven: ceiling_moved` |
| **IOB-PLA-08** | read-only-written | enforced by argv confinement (IOB-CAL-02); a violation is a programming error that fails the run |
| **IOB-PLA-09** | fill vs sequential | `fill_bps` and the sequential 1 MiB rewrite differ by > 5x with unmoved reference -> `fail` for BPS caps |
| **IOB-PLA-10** | cache state | `info`/`unproven` per IOB-CCH-04 |
| **IOB-PLA-11** | shape fit | `linear_model_fit == poor` -> model keys `unproven: shape_fit_poor` |
| **IOB-PLA-12** | noise floor | sweep noise > 4 -> writeback `unproven: noise_floor_high` |
| **IOB-PLA-13** | fresh vs rewrite | `fresh_iops > 1.5 x rewrite_iops` in an interleaved pair with unmoved reference -> `fail` (impossible without a cache effect); moved -> `info`, `unproven: ceiling_moved` |
| **IOB-PLA-14** | data integrity | NaN, duplicate or non-monotonic timestamps in any series -> run `failed` |
| **IOB-PLA-15** | identity stable | identity differs at the end -> run `failed` |
| **IOB-PLA-16** | extent integrity | any writer outside the planned extents or in the reserved tail -> run `failed` |
| **IOB-PLA-17** | outside history | steady differs from the ceiling median by > 3x -> `info` warning |

*Oracles:* `test_pla_NN_controlled_wrong` for each row and `test_pla_healthy_and_moving_cap_pass`. The IO-BENCH-01 facts (cliff 250-300 GiB, fresh 3.6k vs rewrite 71k IOPS, 4096 MiB/s read cap) are not a runtime check but replay assertions, section 17.

## 14. Safety

* **IOB-SAF-01** Before the first state change the tool writes `/run/iobench/restore.json` (temp + rename + fsync) with every original value, the applied value, its path, and the **names of every file created in the work dir**. *Oracle:* `test_saf_01_journal_before_first_write`.
* **IOB-SAF-02** Restore runs on normal exit, exceptions, SIGTERM/SIGINT/SIGHUP, and at the start of the next invocation (`iobench restore` is public and idempotent). A knob is restored only if its current value equals the journalled `applied` value; otherwise the entry is dropped with `restore.skipped[] = {path, reason: "changed_by_other"}` and a warning (exit 0). Created work-dir files are removed. Transient units carry `RuntimeMaxSec`. *Oracle:* `test_saf_02_restore_on_{exit,exception,signal,next_start}`, `test_saf_02_does_not_clobber_newer_value`, `test_saf_02_removes_leaked_files`.
* **IOB-SAF-03** Restore is verified by read-back; a mismatch is exit 9 naming the file and value; the journal is kept. *Oracle:* `test_saf_03_readback_mismatch_exit_9`.
* **IOB-SAF-04** The tool reads every slice's `io.max` (raw cgroup file) before and after and records `slice_io_max_unchanged`; a difference is exit 9. The Host raises on any write to a path ending `io.max`, any `systemctl set-property` or `daemon-reload`, and any `IO*Max=`/`IOWeight=` argument. *Oracle:* `test_saf_04_io_max_never_written`, `test_saf_04_io_max_changed_by_others_detected`.
* **IOB-SAF-05** The live-host profile refuses (exit 2) with running production containers (`--production-pattern`, default any running container) or active units named by repeated `--production-unit`, unless `--confirm-quiet-window`. Limitation, stated in the refusal text: workloads under systemd units that are not listed are **not detected**; `--confirm-quiet-window` is the operator's responsibility. `--installer` bypasses this check and IOB-SAF-06 only. *Oracle:* `test_saf_05_refuses_with_containers_and_units`.
* **IOB-SAF-06** A live-host run (not `--installer`) REQUIRES `--protect-cgroup` and `--abort-psi` (no defaults: absent => refused, exit 2). Abort: root `io.pressure` `some avg10` of that cgroup above the threshold for 3 consecutive samples, or `/run/iobench/abort`; the run kills its fio children, waits up to 30 s for device `inflight == 0`, restores, marks the run `aborted` (exit 8). *Oracle:* `test_saf_06_abort_paths_and_required_flags`.
* **IOB-SAF-07** An aborted or failed run never replaces `result.json` or `calibration.json`. *Oracle:* `test_saf_07_failed_run_keeps_good_result`.
* **IOB-SAF-08** One iobench run per host at a time (non-blocking lock); a second invocation exits 2 and writes nothing. *Oracle:* `test_saf_08_single_instance`.
* **IOB-SAF-09** Mount and swap evidence is read from the **host** mount namespace: iobench compares `/proc/self/mountinfo` with `/proc/1/mountinfo` and refuses (exit 2) when they differ for any entry touching the target, its holders or the work dir. *Oracle:* `test_saf_09_namespace_mismatch_refused`.

## 15. Profiles, stages and allotments

| Profile | Command | Budget | Content |
|---|---|---|---|
| `quick` | `calibrate --profile quick` | 12 min | S0, S1, fill up to `R_est x 60 s`, rungs 1 GiB / 16 GiB / `WS_max`, reference cells, S6 reduced (4 KiB and 64 KiB x seq/rand x read/write rewrite = 8 cells), S7 at 10/30/90 %; **no CDF runs: all cache-dependent values `unproven: quick_profile`** (reported honestly, not absent) |
| `standard` | `calibrate --profile standard` (default) | 60 min | the table below |
| `thorough` | `calibrate --profile thorough` | 75 min (4500 s) | standard + QD64 (60 s) + the 900 s / 240 s observation runs (replacing 240/120 s, +780 s): 4240 s planned; `--full-sweep` is a matrix option |
| `matrix` | `matrix` | 15 min | S0, S3, 6-cell sweep, S5 |
| `matrix --knob` | `matrix --knob K --candidates ...` | 8 min | A B A B cells |
| `run` | `run` | sum | calibrate if no current calibration, then matrix |

`standard` allotment for the r1002 geometry (seconds; the planner derives it from the geometry, this table is the oracle's expected plan; `thorough` adds 840 s: observation runs +780 s and QD64 +60 s):

| Stage | s | Basis |
|---|---|---|
| S0 identity/preflight | 30 | |
| S1 rate probe | 30 | 20 s + setup |
| fill | 420 | 345 GiB at ~1.36 GB/s = 270 s, +50 % |
| ladder | 840 | 14 rungs (10 + 4 densification) x 2 directions x 30 s |
| largest-rung QD1/QD8 | 120 | 4 x 30 s |
| CDF runs | 480 | 4 rungs x 2 directions x 60 s |
| fresh vs rewrite | 90 | 2 x 30 s + discards |
| reference cells | 150 | 5 pairs x 2 dirs x 15 s |
| observation runs | 360 | 240 + 120 |
| S6 | 660 + 60 | 22 cells x 30 s + anchor repeats |
| S7 | 160 | 4 points x 2 dirs x 20 s |
| **total** | **3400** | <= 3600 (200 s slack) |

* **IOB-PRF-01** `--budget-minutes N` overrides the profile; `--dry-run` prints each stage's allotment, fio argv and written-byte bound and writes nothing. *Oracle:* `test_prf_01_dry_run_plan_complete_and_writes_nothing`.
* **IOB-PRF-02** `quick` marks `limits += quick-profile` and its cache-dependent values `unproven: quick_profile`. *Oracle:* `test_prf_02_quick_statuses`.

## 16. Result, calibration, read path

* **IOB-RES-01** Files follow section 18; writes are atomic (temp + rename + fsync of file and directory), mode 0644 root-owned. *Oracle:* `test_res_01_atomic_write_modes`.
* **IOB-RES-02** Versioning: integer `schema` (major) and `schema_minor`. A reader refuses an unknown major (exit 5, `schema_unsupported`), ignores unknown keys, accepts any minor <= its own (a missing newer optional key is `not_measured`, never a default). *Oracle:* `test_res_02_versioning`.
* **IOB-RES-03** Identity (exit 3 when it differs): `disk.devno`, `disk.partuuid`, `disk.size_bytes`, and each of `disk.model`, `disk.serial`, `disk.wwn` that is non-null in the stored record. `scheduler`, `ram_bytes`, `kernel` major.minor, `root_source`, the work-dir `fs.uuid` and age > 180 days are **advisory** warnings (a point release or a re-mkfs of the work-dir filesystem does not change the measured raw disk). The identity is re-read at the end of the run (IOB-PLA-15). *Oracle:* `test_res_03_identity_validity` (each strict field flipped -> exit 3; each advisory flipped -> warning only).
* **IOB-RES-04** `quality.limits[]` and `quality.cache_proof` (`none|working-set|limited-by-budget`) are recorded; `check` prints them. *Oracle:* `test_res_04_limits_recorded`.
* **IOB-CHK-01** `iobench check [--result P] [--calibration] [--section writeback|caps|iocost_model|iocost_qos] [--json]`: states and exit codes exactly per the tables of IOB-STA-05: 0, 3, 4, 5 (with a `reasons[]` closed vocabulary), 6, 7; JSON `{state, reasons[], warnings[], limits[], identity_strength, sections: {S: {proven: n, unproven: [{key, reasons}], absent: [{key, reason}]}}, result, calibration_id}`. *Oracle:* `test_chk_01_exit_codes` (one case per row) and `test_sta_05_status_collapse_audit`.
* **IOB-CHK-02** `iobench show --proposals|--evidence|--calibration|--ceilings|--paths [--section S] --json` prints the object; exit 4 if the file is absent, 5 if corrupt/unsupported, 3 is not used by `show` (it prints the file and a `stale: true` field); unknown section => 2. It never recomputes nor modifies. *Oracle:* `test_chk_02_show_is_read_only_and_exact` (each exit).
* **IOB-CHK-03** Wizard contract: precedence `benchmark (check == 0, or an explicit operator accept of each unproven value with its reasons shown) > configured value > kernel`; guaranteed keys for schema 1: `proposals.writeback.*`, `proposals.caps.dev_aggregate`, `proposals.iocost_model`, `proposals.iocost_qos`, `quality`. *Oracle:* `test_chk_03_guaranteed_keys_present_on_golden_results` for both replay results.
* **IOB-CHK-04** `IOBENCH_DIR` (default `/var/lib/iobench`) and `--state-dir` select the directory. *Oracle:* `test_chk_04_state_dir_resolution`.

## 17. CLI

* **IOB-CLI-01** Commands: `target init|show`, `calibrate`, `matrix`, `run`, `check`, `show`, `ceilings add|summary`, `restore`, `--version`. Mutating commands support `--dry-run`. Exit codes: 0 ok, 1 runtime failure, 2 refusal or usage, 3-7 per `check`, 8 aborted (restored), 9 restore or io.max verification failure. *Oracle:* `test_cli_01_commands_and_exit_codes`.
* **IOB-CLI-02** A refusal message names the condition and a safe remedy; it MUST NOT prescribe an unsafe or non-existent one. *Oracle:* `test_cli_02_messages`.
* **IOB-CLI-10** `iobench.cli` is registered with `cli_extended` (`CliRegistry`, generated help, `--dry-run`, version from the distribution); the generated CLI surface and review artefacts exist and pass the cli-extended audit. *Oracle:* the cli-extended audit test module and `test_cli_10_surface_matches_registry`.

## 18. Schemas

Timestamps UTC ISO-8601 `Z`; sizes bytes; rates bytes/s or IO/s; latencies microseconds.

### 18.1 `result.json` (schema 1, minor 0)

```json
{
  "schema": 1, "schema_minor": 0,
  "tool": {"name": "iobench", "version": "0.1.0", "method": "ladder-v1+cachefit-v1+shape-v1+latcurve-v1+drain-v1+sweep-v1+readcost-v1"},
  "run": {"id": "run-20261021T031500Z-a1b2", "started": "2026-10-21T03:15:00Z", "finished": "2026-10-21T03:27:41Z", "profile": "matrix", "status": "complete",
          "budget_s": 900, "calibration_id": "cal-20261021T020000Z-7b2d0c1e",
          "stages": {"S0": "ok", "S3": "ok", "S4": "ok", "S5": "ok"}},
  "identity": {"disk": {"devno": "254:7", "model": null, "serial": null, "wwn": null, "partuuid": "7b2d0c1e-07", "size_bytes": 435853320192, "scheduler": "none", "rotational_flag": "1"},
               "identity_strength": "weak", "root_source": "/dev/vda3", "workdir_fs": {"type": "ext4", "uuid": "00000000-0000-0000-0000-000000000000"}, "kernel": "7.2.9", "ram_bytes": 8321499136, "virt": "kvm"},
  "targets": {"W_s": 2.0, "P_ms": 250, "F_read_ratio": 0.25, "U_dev_share": 0.70, "source": "default"},
  "evidence": {"rate_probe": {}, "drain": {}, "sweep": {"noise_floor_ratio": 1.9, "cells": []}, "read_cost": {"native_bps": 0, "candidates": []},
               "preflight": {"containers_active": 0, "confirmed_quiet_window": true, "slice_io_max_before": {}, "slice_io_max_after": {}}},
  "proposals": {"writeback": {}, "caps": {}, "iocost_model": {}, "iocost_qos": {}, "notes": []},
  "quality": {"cache_proof": "working-set", "limits": ["bucket_exclusion_s<=240"], "checks": [{"id": "IOB-PLA-01", "status": "pass", "detail": "", "degrades": []}]},
  "restore": {"verified": true, "slice_io_max_unchanged": true, "journal_removed": true, "skipped": []}
}
```

Required top-level keys: `schema, schema_minor, tool, run, identity, targets, evidence, proposals, quality, restore`. `run.status` in `complete|aborted|failed`. A proposal value:

```json
{"value": 16777216, "status": "unproven", "reasons": ["worst_from_single_run"], "rule": "R1", "inputs": {"W_s": 2.0, "R_drain_bps": 10500000, "inflight_bytes": 1048576, "T_eff_bytes": 1717986918}, "basis": "confirmed-by-sweep", "flags": []}
```

### 18.2 `calibration.json` (schema 1)

Same envelope (`schema, schema_minor, tool, run, identity, quality`) plus:

```json
{
  "calibration": {"id": "cal-20261021T020000Z-7b2d0c1e",
    "target": {"kind": "device", "path": "/dev/vda7", "size_bytes": 435853320192, "usable_bytes": 435852271616, "reserved_tail_bytes": 1048576},
    "extent_written": [[0, 370472800000]], "fresh_extent": [370472800000, 378000000000], "fill_bps": 1357000000, "R_est_bps": 1357000000,
    "ladder": {"rungs": [{"ws_bytes": 1073741824, "dir": "read", "qd": 32, "steady_iops": 97850, "mean_iops": 98100, "state": "steady", "observation_s": 30, "rate_limited": "not_assessed", "class": "cached_tier", "lat_us": {"p50": 309, "p90": 420, "p95": 480, "p99": 545, "p999": 692}, "series": [[0, 97000]]}],
               "cliff_bytes": 322122547200, "cached_ref_iops": 92000, "qd90": {"read": 8, "write": 8}, "idle_qd1_p99_us": {"read": 272, "write": 440}, "cached_tier_factor": 3.8},
    "cache": {"read": {"cache_state": "undetermined", "E1": {"fit": "insufficient", "points": 1}, "E2": {"shape": "overlapping", "gap_ratio": 1.8}, "hit_ref_us": 109},
              "write": {"cache_state": "undetermined"}},
    "cap_basis": {"read": {"iops": 23922, "status": "unproven", "reasons": ["cache_model_undetermined"]}, "write": {"iops": 21094, "status": "unproven", "reasons": ["cache_model_undetermined"]}},
    "fresh_vs_rewrite": {"measured": true, "fresh_iops": 3596, "rewrite_iops": 71584, "penalty": 19.9},
    "reference": {"read": [{"t": "2026-10-21T02:05:00Z", "iops": 24100}], "write": [], "moved": false},
    "shape": {"cells": [], "fit": {"read": {"rand": {"c_io_us": 0, "c_byte_ns": 0, "r2": 0, "residual_max": 0}}}, "metering": "undetermined", "linear_model_fit": "good", "override": null},
    "latency_curve": [{"dir": "write", "load_pct": 10, "bps": 0, "lat_us": {"p50": 0, "p90": 0, "p95": 0, "p99": 0}}],
    "latency_knee": {"read": null, "write": null},
    "iocost_keys": {"rbps": 0, "rseqiops": 0, "rrandiops": 0, "wbps": 0, "wseqiops": 0, "wrandiops": 0}}
}
```

### 18.3 `ceilings.jsonl`

```json
{"header": true, "schema": 1, "identity_digest": "sha256:0000000000000000000000000000000000000000000000000000000000000000"}
{"ts": "2026-10-21T03:20:00Z", "dir": "write", "workload": "seq1m", "plateau_bps": 14680064, "duration_s": 600, "source": "governor"}
{"ts": "2026-10-21T04:10:00Z", "dir": "write", "workload": "rand4k", "plateau_bps": 86000000, "duration_s": 15, "source": "benchmark", "note": "reference_shift"}
```

### 18.4 Journal and marker

`/run/iobench/restore.json`: `{"run": id, "started": ts, "entries": [{"path": "...", "original": "...", "applied": "..."}], "files": ["workdir/..."]}`. Marker: the last 1 MiB of the target; its first 4 KiB is JSON `{"magic": "IOBENCH-TARGET-1", "size_bytes": N, "partuuid": "...", "serial": null, "created": ts}` followed by zero padding.

## 19. Test, gate and documentation plan

* **IOB-EVD-01 (replay)** `tests/fixtures/` holds condensed, provenance-stamped fixtures: the recorded r1002 rungs ≤ usable (header: sha256 of `IO-BENCH-01-data/r1002-fio-out.tgz`) and the gstammtisch tables (HOST-IO-INTERFERENCE s4.3/4.4), plus the **synthetic** `r1002-model` (C = 265 GiB, F = 92k, R = 9.8k; reproduces the recorded 300 GiB point 46440 vs 46315). Required replay oracles: gstammtisch -> 16 MiB / 4 MiB / 16 / 64; r1002 -> `kernel` for dirty and sectors; recorded r1002 cache state `undetermined` (honest, CCH-06 #10); synthetic -> `cliff_modeled`; the IO-BENCH-01 facts as assertions (cliff in [250, 300] GiB, fresh 3.6k vs rewrite 71k, cached-tier factor in [3.5, 4], 4096 MiB/s read flagged `rate_limited`). *Oracle:* the named tests.
* **IOB-EVD-02** A fixture round-trip is not an oracle: every replay test asserts a derived value that depends on a rule constant. *Oracle:* `test_evd_02_mutation_sanity` mutates each rule constant (monkeypatch) and asserts the corresponding replay test fails.
* **IOB-ACC-01 (acceptance probe, no VM lane, ruling R-10)** The live acceptance of mechanics and plausibility is the r1002 run of IOB-03 P4 (target guard refusals on real devices, sysctl/sysfs writes landing and restoring, `check` after a real run). It is an **acceptance probe**, not a unit test; it is recorded under `measurements/` in the program directory.
* **IOB-GATE-01** `run-gate.toml` registers `r0-r1` (pytest, 100 % branch coverage; `.coveragerc` omits only `host.py` and `__main__.py`, each with a reason, and `test_gate_01_coverage_exclusions_match` asserts the omit list equals exactly those files) and `r2` (assay mutation lane, `assay.toml` `whole_target` over every shipped module, kept in step by `test_gate_01_mutation_targets_cover_every_module`, which exempts only `host.py` and `__main__.py`) and an aggregating `gate` lane. The devcontainer is a cockpit. The `vm` marker is not used. *Oracle:* `./run-gate.py r0-r1` exit 0 from the marker file (probe).
* **IOB-DOC-01** Docs-sync: every requirement id in this file has a collected test **or** is listed in `tests/probe_oracles.txt` (the acceptance probes: IOB-ACC-01, IOB-GATE-01, IOB-REL-01); every collected iobench test id exists here; every cross-document anchor resolves. *Oracle:* `test_doc_01_ids_anchors`.
* **IOB-DOC-02** In README, DESIGN-GUIDE and CONSUMERS (the SPEC is the contract), every `json` block parses with the shipped loader as the object it claims, every `bash` `iobench` invocation parses with the CLI's own parser in `--dry-run` mode, and every value of the closed vocabularies of IOB-STA-02, the exit codes 0-9 and the check ids IOB-PLA-01..17 appears in at least one of the three. *Oracle:* `test_doc_02_examples_parse_and_vocabulary_complete`.
* **IOB-REL-01** Release through cmru only: `libraries/iobench/cmru.toml` (`iobench-v*` tags, wheel plus zipapp plus sha256, GitHub Releases only, never PyPI), `fio >= 3.30` floor with its reason documented. *Oracle (probe):* `cmru status` reports the project and `cmru release --dry-run` plans the wheel.

## 20. Out of scope for IOB-03 and open items (owner)

Out of scope: consumer wiring (M1-M4), the persistent long-term probe (D-011), per-tier caps, changes to the shipped `iocost_coef_gen.py` flow.

| Open item | Owner |
|---|---|
| OI-1 installer (M2): iobench never makes a filesystem, so the installer needs either two throwaway partitions (raw target + work-dir fs) or a calibrate-then-mkfs sequence with a documented marker-preserving `mkfs` size | M2 work item (controller) |
| OI-2 `history: single-run` hosts have no automatic population of the ceiling history until the probe/governor (D-011) exists | D-011 owner |
| OI-3 the ten-millisecond request-time constant of R3 is `[I]` and the sweep is its arbiter | P4/P5 evidence |
