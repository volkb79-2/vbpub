# Review IOB-03 P3, scope B: measurement, estimators, derivations, proof model

Target: `libraries/iobench`, worktree `/workspaces/vbpub/.worktrees/iob-03-20261021`, branch `iob-03-20261021`, HEAD `30a847beb`.
Method: read-only. No edits, no pytest, no gates. Two throw-away `python3 -I` snippets (arithmetic hand-checks of `series.reduce_series`, `cache.write_time_proof`, `cache.modes`) were run against `src/`; their numbers are quoted below.
Paths are relative to `libraries/iobench/src/iobench/` unless stated.

## Verdict: NOT-READY

7 BLOCKING, 17 SHOULD, 12 NIT. Every BLOCKING item is a way for a value to be labelled `proven` (or a check to certify) when its basis does not prove it, or a spec-mandated check that never runs.

## What I verified and found correct (so it is not re-reviewed)

* **Planner sums.** r1002 geometry (size 435853320192, R_est 1.36 GB/s, 3600 s): `ws_max` = `int(0.85*usable)` = 345.04 GiB; rungs 1,2,4,...,256, WS_max (10) + 2 densification = 12; ladder 12x2x30 = 720; fill = ceil30(1.5*ws/R_est) = 420. Stage sum: 30+30+420+140+720+120+120+90+120+100+150+360+720+160 = **3280**. Thorough: +60 (QD64) +780 (observation 1140 vs 360) = **4120**. Quick sums to 670 <= 720. Matrix typical 650 / worst 890; knob (2 candidates, repeat) 220 / 420. All match SPEC.
* **Derivations by hand** (`derive.py`):
  * R1 gstammtisch: W=2, R_drain=10.5e6, inflight = 16x64x1024 = 1 MiB; budget = 19,951,424 B = 19.03 MiB; pow2 floor 16 MiB; R1b = 4 MiB. Correct. Inflight-exceeds-budget branch and the "kernel" branch are correct.
  * R3: 10.5e6 x 0.010 / 1024 = 102.5 -> 64 KB. Correct. r1002 -> candidate clamps to `max_hw` -> `"kernel"`. Correct.
  * R2: `pow2_ceil(2x8)` = 16, clamped [8,64], "kernel" if not below current. Correct.
  * R5: 0.70 x 9400 x 0.43 = 2829.4 -> 2829. Correct.
  * R6 byte: 6291456 B/s -> `wseqiops` = 6291456 // 4096 = 1536. Correct.
  * QoS max: 110 x (92000/9800 = 9.388) = 1032.7 -> `ceil_to(...,5)` = 1035. Correct. gstammtisch write 18/6 = 3 -> 330. wlat 4.2 ms -> 10 ms, 17 ms -> 35 ms; rlat 0.6 ms -> 2 ms, 3.2 ms -> 7 ms. All correct.
  * `ceil_to`'s 1e-9 epsilon prevents the exact-multiple trap.
* **DER-08.** `best_read_rung` is the only cached_tier consumer: it feeds `qos_max` inputs and `read_cache_headroom` only (`proposals.py:394`, `calibrate.py:406,409`). No cached figure enters a cap or a model key directly. (But see B6 for a cache-contaminated figure entering the model by another door.)
* **E-cold ordering and confinement.** Stage order is `fill -> E-cold -> ladder` (`calibrate.py:606-610`); `reference("r0")` runs inside `run_ladder`, after E-cold. Nothing touches `[0, cold_len)` between fill and E-cold (S1 wrote `[0,1 GiB)` before the fill, which then overwrote it). QD32 is `[0, half)`, QD1 is `[half, cold_len)`: disjoint; both are inside `read_extent` and checked by `Runner._check_job`.
* **E2 histogram split, hand-checked** with FakeFio's own `mixture()` through production `cache.modes()`:
  * 0.6 @ 110 us + 0.4 @ 760 us -> bimodal, modes [110, 758], h2 = 0.600, valley ratio 0.0.
  * 0.77/0.23 -> h2 0.77.
  * 0.6 @ 110 + 0.4 @ 200 -> overlapping (gap 1.82, valley ratio 0.11), h2 not produced.
  * 0.97/0.03 -> unimodal (mass < 5 %).
  `R_E2 = steady(QD32) x (1 - h2)` is the miss-throughput identity, so the formula is sound. Adjacency: `e2()` runs immediately after the largest QD32 rung; start-to-start gap ~30 s + overhead, voided above 60 s (an extended 300 s rung voids it by construction, which is conservative).
* **E1.** Cannot raise on the degenerate inputs (`sxx==0`, `a<=0`, `denom==0`, `n<4`, `f_cached` falsy are all guarded); it never feeds `classify_cache`; it can only supply an *unproven* value when no cold probe exists (`read_value`).
* **Metering.** Both tests are required for `byte` and `iops` (`shape.py:301-304`); fresh cells are excluded from the fit and the tests (`block_state == "rewrite"`), so a fresh penalty cannot change `metering`.
* **`no_cache_observed`** is never `proven`: the branch sets `reasons=["cache_not_excluded"]`, `r=None` (`cache.py:277`), `ratio` for BPS is only computed when the state is proven (`calibrate.py:456`), `headroom` is None. Verified through caps, BPS, QoS max and headroom.
* **`check` exit 0/7 scoping** works per requested section for sections that exist (`result.py:241-245`); an unknown *major* is refused (`result.py:84`); required top-level keys are enforced.
* **FakeFio cache model is genuinely content-based** (`testing.py:42-128, 335-424`): per-sample hit fractions are computed from the LRU bucket map at that moment, every job mutates it (`_touch_cache`), reads mix hit/miss harmonically, write-allocate/read-allocate are separate flags. It is not the closed formula. For `fill_history_inversion` the assertion `cap <= R_cold` is true by `min()` construction, but the *placement* (E-cold lands in an evicted region, `cached_ref` becomes the miss rate, no cliff, rising ladder) comes from the simulated content, so that test is not a tautology.

---

## BLOCKING

### B1. The write time-axis proof can pass when it should not: burst bytes are under-counted by the median smoothing
`series.py:141,150-156` (burst from `rolling_median(values, 5)`), `cache.py:324-333`.
`burst_units` integrates the *smoothed* rate. A rolling median of window 5 removes bursts of <= 2 samples. Measured with production code on the CCH-07 gstammtisch shape (2 GB/s for 2 s then 14.68 MB/s, 1 s samples, bs=1):

| observation | burst per code | true burst | after-knee bytes | code | correct |
|---|---|---|---|---|---|
| 240 s | 2.978e9 | 3.971e9 | 3.48e9 | unproven | unproven |
| 500 s | 2.978e9 | 3.971e9 | 7.30e9 | **proven** | unproven (needs 7.94e9) |
| 540 s | 2.978e9 | 3.971e9 | 7.88e9 | **proven** | unproven |
| 560 s | 2.978e9 | 3.971e9 | 8.18e9 | proven | proven |

So the 240 s / 600 s oracle passes, but between ~405 s and ~543 s the proof says `proven` while after-knee bytes are < 2 x the real burst. The worse case: a **one-sample burst is invisible**: `gst(240, burst 1 s at 5 GB/s)` returns `state=flat, knee_s=0, burst_units=0`, `write_time_proof(...).proven == True` although the true burst (5 GB) needs 10 GB after the knee and only 3.5 GB was observed. The SPEC example numbers (`burst_bytes 3970000000`, `knee_s 2`) are the true values, not what this code produces (2.98e9, knee 3), and the test (`test_cch_07`) asserts only the two qualitative endpoints, so the discrepancy is not caught.
Estate rule: the comparison (smoothed integral) is narrower than the claim ("bytes after the knee >= 2 x the burst").
**Fix.** Compute proof burst from raw samples: `burst = sum over i < knee_index of (raw_i - steady) * dt_i` (signed sum, noise cancels, no median bias), plus include sample 0 in the integral even when the smoothed series calls the run `flat`. Alternatively define `flat` only when raw max over the first 10 % is within the band. Add oracles: gstammtisch at 500 s and 540 s -> unproven; one-sample 5 GB/s burst at 240 s -> unproven; assert `burst_bytes ~ 3.97e9` for CCH-07 (b).

### B2. An unsteady cold probe is labelled `proven` (`cold_measured` / `rate_cap`)
`calibrate.py:224,393`, `cache.py:147-149,266-272`.
`e_cold` calls `self.reading(qd32)` which returns a `Reading` with `state` possibly `unsteady` (tail CV > 0.2, trend > 0.2, or no knee). `ColdProbe` keeps only `reading.steady`, `rate_limited`; the state is dropped. `classify_cache` then proves `cold_measured` or `rate_cap` from a drifting series. Every other basis in the run is guarded (`largest["state"] == "unsteady"` -> `absent: basis_unsteady`); the one value that confers `proven` on all read caps is not. STA-05's `proven` row says "the value's basis is steady (`steady`/`flat`)". A read-allocate cache warming during the 65 s probe, or a decaying provider bucket, yields a trend and a mid-decay "R_cold" that becomes the proven cap.
Related: `series.rate_limited` (`series.py:117-128`) accepts *any* 60 s window with CV < 1 %. A 65 s cold series that is flat for the first 60 s and then changes in the last 5 s is `rate_limited=True` (hence `rate_cap`, proven) with `steady` taken from the changed tail.
**Fix.** Carry `state` into `ColdProbe`. If not `steady`/`flat`, state `undetermined`, reason `cache_model_undetermined` (or extend the probe once like a rung). Require `rate_limited` to hold on the tail window that defines `steady`, not on any window. Oracle: a cold series with a 20 % linear drift and a flat-first-60-s series must not reach `proven`.

### B3. The proven write-IOPS cap is a different quantity from the series that was time-proven
`calibrate.py:432-446`, `ladder.py:279-283`.
`iops = write_iops_basis(rewrite, fvr)` = min of two **30 s QD32** readings (`fresh_vs_rewrite`). The proof (`write_time_proof`) is computed on the **QD8 240 s / 120 s observation** series. Nothing bounds the value by the observation's steady. A bucket whose burst lasts 60 s at 2 x sustained: the 30 s runs (5 s discarded) sit wholly inside the burst and report ~2L; the 240 s observation has knee 60, burst bytes 60L, after-knee bytes 180L >= 120L -> `proven`. The cap is then 2 x the sustained rate and carries `proven`. (`test_cch_07` and `test_sta_05` `short_observation` never construct a burst that the 30 s runs fully contain.)
**Fix.** `iops = min(rewrite, fresh, obs["write"].steady, obs["fresh"].steady if fresh in basis)`; record all inputs in `cap_basis`. Add an oracle for the 60 s-burst scenario above expecting `value ~ L`.

### B4. The write basis is `proven` with missing or moved inputs
`calibrate.py:410,432-446,442-445,520`.
Three paths to `proven`, each with an input absent or contested:
1. `fresh_in_basis(fvr)` is true but `"fresh" not in self.obs` (the `observation` stage failed after `obs["write"]`/`obs["read"]` were set, e.g. a `SeriesError` on the fresh run). `self.proof` then holds only `rewrite`; `all(p.proven ...)` is true; the cap `min(rewrite, fresh)` is `proven` with the fresh half never time-proven.
2. The `fresh-vs-rewrite` stage is skipped for budget or fails (`fresh_vs_rewrite()` deliberately raises `SeriesError("no fresh extent")` for control flow, so it is recorded as `failed: unusable_series`, not as a skip). `self.fvr is None`; the basis falls back to the ladder `rewrite`; no reason is added (reasons come only from `extras`, `quick`, budget and proof). The cap is `proven` without the fresh penalty (3.6k vs 71k IOPS on r1002: 20x). The calibration then reports `fresh_vs_rewrite: {"measured": False, "reason": "quick_profile"}` on a standard profile (`self.fresh_note or "quick_profile"`), a false reason.
3. `extras` only includes `ceiling_moved` when `moved["ladder"]`. The write basis is built from the fresh-vs-rewrite comparison group, whose reference pair is `moved["fresh_vs_rewrite"]`; that flag affects only PLA-13 (and only when fresh > 1.5 x rewrite). REF-02: "A comparison whose reference moved is `unproven: ceiling_moved`".
**Fix.** (1) require `"fresh" in self.obs` whenever `fresh_in_basis`, else `short_observation`. (2) if the device kind supports discard and `fvr is None` add an unproven reason (`budget_limited` for a budget skip, `stage_skipped`-class otherwise; extend the closed vocabulary if none fits) and fix the reason text to the real cause (`fresh_note`, or `stage_failed`). (3) add `moved["fresh_vs_rewrite"]` to the write extras and `moved["shape"]` to the BPS extras (already there). Oracles for each path in `test_sta_05`.

### B5. `check` certifies a non-existent or unrecognised section as proven
`result.py:238-245`, `proposals.py:270-280`.
* `group.get(name, {})` with an absent section returns `{"proven": 0, "unproven": [], "absent": []}`, which counts as "nothing wrong". `iobench check --calibration --section writeback` (calibration.json carries no `writeback`) exits **0, `current`**. `test_chk_01_calibration_check_uses_the_calibration_file` asserts exactly `sections["writeback"]["proven"] == 0` and accepts `current`. This is estate anti-pattern 2 (absence for emptiness) and 4 (superset): "no proposals" and "nothing unproven" collapse into exit 0. An empty `proposals.writeback = {}` in a result does the same.
* `section_summary` treats every status that is not `absent` and not `unproven` as proven (`else: out["proven"] += 1`). A status from a newer minor (or a damaged file) such as `"provisional"` counts as proven. `check` also accepts a *newer* minor, contrary to IOB-RES-02 "accepts any minor <= its own".
**Fix.** Missing or empty requested section -> `absent` entry (`no_calibration`/`stage_skipped`) so the exit is 7, or refuse with exit 2 for `--calibration --section writeback`. Whitelist `proven` explicitly; any other status -> `corrupt` (exit 5). Refuse `schema_minor` above the reader's, or ignore only unknown *keys*, and test both.

### B6. Read-model IOPS keys are derived from cache-contaminated S6 cells and still `proven`
`proposals.py:324-374`, `derive.py:101-108`, `shape.py:238-244,318`.
For `metering` in `mixed`/`undetermined` the read keys are `rseqiops = 1e6/c_io_seq_us`, `rrandiops = 1e6/c_io_rand_us`. The S6 read cells run on `[0, ws)` (the full extent, the same mix of hits and misses as the largest rung), so `c_io` encodes the *cached* rate. `rbps` is scaled by `cache_ratio` through the BPS basis, the IOPS keys are not. The reasons list is `bps_basis["reasons"]` (empty when the cache state is proven), so for `mixed` the cached-rate keys are `proven`. SPEC DER-05/-08: the read basis is the proven cache-free `R`, never the cached rate. In `byte` and `iops` classes the keys derive from cache-free bases and are fine.
Related: `fit_cells` and `metering_for` include `unsteady` cells (the only state filters are in `bps_bases.cell()` and `windows`), contrary to DER-08.
**Fix.** For reads, cap the IOPS keys by the cache-free read IOPS basis (`min(1e6/c_io, iops_basis["value"])`), or mark them `cache_residual_possible` unless scaled; drop `unsteady` cells from the fit (fit `insufficient`/`absent: basis_unsteady`). Oracle: a mixed-metered disk with a 3x cache speed-up must give `rrandiops <= R`.

### B7. PLA-07, PLA-09, PLA-17, PLA-18 are never evaluated by a run
`plausibility.py:101,116,152,161` are defined and unit-tested, but no production caller exists (`grep plausibility\.` over `src/`: only PLA-01..06, 10, 11, 13 in `calibrate.py`, PLA-12 in `matrix.py`; PLA-08 as `IntegrityViolation` in `stages.py:146`). Consequences:
* PLA-07 (random-4K bytes/s > sequential) and PLA-09 (fill vs sequential 1 MiB differ > 5x) never degrade the BPS caps or the model; an impossible byte figure passes silently.
* `quality.checks[]` never contains PLA-17/18; `rearm_ratio` is stored in `calibration.rearm` but PLA-18's `fresh_write_bps` is never recorded. SPEC section 13 says "Each check has a controlled-wrong oracle"; here the oracle proves only the pure function, so the test is green while the run-level check does not exist.
**Fix.** Wire them in `analyze`/`assemble` (inputs are all available: `cells`, `fill_info["fill_bps"]`, `history`, `rearm_info`, `windows`) and add run-level controlled-wrong scenarios; `Scenario.little_factor` exists for PLA-06 but no test sets it, so PLA-06 and PLA-13 run-level wiring is untested too.

---

## SHOULD

### S1. QoS `max` can be `proven` while the cap it depends on is unproven
`proposals.py:381,394-397`. `reasons` is only `read_value.reasons` (cache state) + `worst_from_single_run` + `shape_fit_poor`. It omits `budget_limited` and `ceiling_moved` that `cap_basis.read` carries (`calibrate.py:410,425`). With history present (so `worst_from_single_run` is gone), a truncated ladder or a moved reference leaves `max` `proven`. `best_read_rung` (`ladder.py:259`) also maxes over `unsteady` rungs. **Fix:** derive `max` reasons from `cap_basis.read.reasons`; exclude `unsteady` rungs from `best_read_rung`.

### S2. `wlat`/`rlat` are unconditionally `proven`
`proposals.py:401-410`. The S7 load points are 10/30/60/90 % of `bases[dir]["iops"]` even when that ceiling is `unproven`; a 20 s light-load write run on a write-back cache can sit entirely inside a burst. **Fix:** inherit the ceiling's reasons (a value is not stronger than its input).

### S3. S6's "before" reference is stale
`calibrate.py:333,368`, `body` order. `r3` is taken at the end of `rearm`, then `observation` (240 s + 120 s + 120 s of heavy writes, which is exactly what drains a bucket), then S6, then `r4`. On a draining-bucket disk `r4 != r3` by construction, so `moved["shape"]` fires on the *healthy* gstammtisch state and every BPS cap and model key becomes `unproven: ceiling_moved`. The estate rule: a refusal needs its legitimate state constructed. **Fix:** take a fresh reference immediately before S6 (+30 s fits in the 320 s slack; update the allotment table and `test_slo_05`).

### S4. `qd90` and `R2` from unsteady depth rungs
`calibrate.py:253-259,486-487`. `depth[direction][qd] = rec["steady_iops"]` with no state check (extension only for QD32). R2 `nr_requests` is then `proven`. **Fix:** any `unsteady` depth rung -> `qd90 = None` (R2 `absent: basis_unsteady`).

### S5. Optional estimator errors are fatal
`cache.py:30` (`HistError(ValueError)`), `calibrate.py:224,242,281`. An empty or missing histogram raises `HistError`, which `Runner.stage` does not catch (it catches `SeriesError` only), so E-cold and hit_ref crash the run with a traceback. `self.e2(...)` is called directly inside the *required* `ladder` stage (not wrapped by `self.stage`) and then `self.mark("E2","ok")` regardless; any exception there fails the calibration, though CCH-03 says a missing E2 "is not a failure". **Fix:** make `HistError` a `SeriesError`; wrap E2 in `self.stage("E2", ...)`.

### S6. Model uses the first cell, not the worst window
`proposals.py:361`, `calibrate.py:500`. DER-05: basis = the *worst plateau* (history worst, "else this run's lowest steady window with `worst_from_single_run`"). Without history the code takes `bps_basis["value"]` = the first seq-1M cell; the three write anchor windows (`windows.write_seq1m_bps`) are computed and only used by QoS. A draining bucket makes the first cell the highest. **Fix:** `worst_bps = min(windows)` when no history.

### S7. `TypeError` in the `iops`-metering branch
`proposals.py:364-365`: `min(hist_iops/4096.0, basis_iops_val)` with `basis_iops_val is None` (iops basis `absent: basis_unsteady`) and a ceiling history present raises after the whole calibration. **Fix:** test `basis_iops_val is None` before the `min`.

### S8. Matrix measurement defects
* `matrix.py:188,194`: `self.apply(...)`'s `False` (kernel rejected the knob) is ignored in S5; the read job then runs at the *unchanged* setting and is recorded under the candidate, so `read_ratio` ~ 1 and the read floor is "met". **Fix:** treat `False` as a skipped candidate; read the knob back after `set_knob`.
* `matrix.py:163-175,213-214`, `calibrate.py:353-368,371-382`: no intra-stage budget check. `--knob` with 3 candidates and `--repeat` is 6 cells = 330 s typical / 630 s worst against the 480 s knob budget and runs to completion; S4/S6/S7 loops likewise. SLO-04 "a hard wall-clock budget bounds the run" does not hold inside a stage. **Fix:** `fits()` per cell, record `budget_limited`, never decide on a partial set.
* `calibrate.py:154` (`start_budget` after S0 and S1; `matrix.body` after S0): the budget excludes S0/S1 (~60 s) although the allotment table counts them. Start it at `Runner.started`.
* Matrix writeback R1/R1b/R3 never inherit the calibration's write-proof status (`cap_basis.write` = `short_observation`/`budget_limited`); `R_drain` over a 60 s buffered load can sit inside a burst, and the consistency check compares it against the *unproven* ladder figure. Add `short_observation` when the calibration's write basis carries it.

### S9. `dryrun.rate_source` takes R_est from any `calibration.json`
`dryrun.py:206-208` reads `R_est_bps` from the stored calibration without checking identity or target path. A dry run for another disk plans with this disk's rate: a literal standing in for a fact (AGENTS anti-pattern 1). **Fix:** require `res.check(...)` not stale and `calibration.target.path == cfg.target`, else refuse.

### S10. `ceilings.append` can refuse after the result is already written
`calibrate.py:588-599`, `ceilings.py:341-344`. On a replaced disk the old `ceilings.jsonl` is *ignored* on read (`load`) but *refused* on append, raising `Refusal` from `conclude()` after `calibration.json` and `result.json` are written and before the return: exit != 0 with a complete record. **Fix:** rotate the foreign history aside (or write the observations under the new digest with a warning) before the run, never after.

### S11. `rate_cap` is proven by a flat cold probe alone
`cache.py:266-267`. R-11's corrected reasoning ("no cache and a cache >= fill give one observation, so never proven") applies equally to a flat cold probe: a limiter capping *cache-resident* data (cache >= fill) is indistinguishable from a provider cap on the backing store, and `rate_cap` ignores E2 even when E2 is bimodal and disagrees (STA-03: "with the E2 cross-check when E2 is available"). I understand this is a coordinator ruling; at minimum apply the E2 tolerance to `rate_cap`, and record it as an accepted residual in DESIGN-GUIDE and `limits[]`.

### S12. BPS write is `proven` on a proof of a different workload, and its reason is generic
`ladder.py:339`, `calibrate.py:460`. The write proof covers 4K random QD8 IOPS; a bytes-metered bucket drains at a different rate for 1 MiB sequential. The only reason offered is `short_observation` whatever the cause (quick profile, basis absent, moved), and `write_proven` defaults to `True` in `bps_basis` (a default standing in for a fact; the caller does pass it). Quick-profile BPS read reasons are `cache_residual_possible`, not `quick_profile` (PRF-02). **Fix:** make `write_proven` required; apply `write_time_proof` semantics to the seq cell's own series or mark `unproven` with the real reason.

### S13. PLA-06's failure does not exclude the rung it fails on
`plausibility.py:98`, `calibrate.py:419-420`. Effects: `caps` -> absent only; the faked rung still feeds `best_read_rung` (QoS max), `cached_ref`, the cliff, QD90. Only the first failing check is stored. **Fix:** degrade `iocost_qos` as well, store every failing rung.

### S14. E2 timestamps are not recorded
`cache.py:160` `E2.as_dict` omits `qd32_start`/`qd1_start`, which CCH-03 and the §18.2 example require ("both start timestamps recorded"); `e2_raw` has them. The adjacency evidence is therefore not auditable from the artefact.

### S15. The two-tier "E1 good but wrong" fixture is a precedence test, not evidence
`tests/test_cch.py:374-397`. The ladder is computed analytically and the cold probe is a literal (`cold["r_cold"] * 1.03`); the test proves `classify_cache`/`read_value` prefer the cold figure, which is true by construction. It does not show that E-cold *measures* 9.8k on such a device. The E1 `good` label is also tuned to the estimator (the 324 GiB rung is dropped; LOO 0.133 vs limit 0.15). `CacheModel` already supports `tier2_capacity_gib`: add a dynamic two-tier run through `Calibrator` asserting E-cold ~ backing rate, even if E1 is not `good` there.

### S16. `proven` read BPS/RIOPS caps stay `worst_from_single_run` forever
`ceilings.py:392-396` appends only `rand4k` plateaus; nothing the tool writes ever populates `seq1m`, so `caps.RBPS/WBPS` and the model keys can never leave `worst_from_single_run`, and `check` exit 0 is unreachable for them without a manual `ceilings add` (the tests seed `seq1m` by hand). Either append the seq-1M windows (`windows.write_seq1m_bps`) as `benchmark` observations (CEI-05 says "per direction and workload") or document that exit 0 needs history for BPS keys.

### S17. Fresh-extent stage mislabelled as failure
`calibrate.py:307-313`. Control flow through `SeriesError` records "no fresh extent" / "discard unsupported" as `failed: unusable_series`. That text is a lie for a device that simply has no discard. Record `skipped: no_discard` / `no_fresh_extent` and surface it as a `limits[]` or reason (see B4).

---

## NIT

* N1. Schema drift from SPEC section 18: `cap_basis.read/write` carry `value`, SPEC shows `iops` (`calibrate.py:448,483`); `reference` is `{cells, moved: {group: bool}}`, SPEC shows `{read:[...], write:[...], moved: bool}`. CAL-06 says "QD16 cell", code and SHP-01 use QD32 (documented). Align SPEC or code.
* N2. `plan.densification_rungs` returns un-aligned ints (`plan.py:69`); align to MiB.
* N3. `cache.modes` raises `IndexError` if no peak survives (all mass in bins < 64 ns, whose log-width is 0) (`cache.py:107-113`).
* N4. `check` raises `KeyError`/`IndexError`/`JSONDecodeError` on nested-key damage or an empty/garbled `ceilings.jsonl` header (`result.py:210-229`, `ceilings.py:350`) instead of exit 5; `live_identity` maps `OSError` to `stale` (`result.py:132`), not `unreadable`.
* N5. PLA-14 "any series" is enforced only where `reduce_series` runs. S1's `median(series[-10:])`, the fill `rates`, and drain samples accept NaN (`calibrate.py:147,190`); validate in `devio.parse_rate_log`.
* N6. `sweep.beats_ref`: noise ratio can be exactly 1.0 (identical REF p99s) so any 1 % improvement plus a repeat wins; add a floor (e.g. 1.2).
* N7. `sweep.drain_rate` with < 2 samples -> `StatisticsError` (`sweep.py:30-34`); `matrix.s3` compares `R_drain` with the seq-1M bytes/s basis, not `min(rewrite, fresh) x 4096` as DRN-01 says (`matrix.py:111-112`).
* N8. Only the two write anchors are repeated (`shape.py:233`), so read-direction drift is undetected (documented in IMPLEMENTATION-NOTES; consider noting it in `limits`).
* N9. `derive.r3`: a missing probe p99 for the raised candidate is treated as "no violation" (`derive.py:75-78`); absence passes. Prefer `unproven: formula_only`-class.
* N10. `rate_limited` window check uses 30+ points assumed; on sparse series a window with 1-2 points qualifies (`series.py:123-127`). Require >= 8 points per window.
* N11. `result.load` accepts any `schema_minor`, including a newer one (see B5) and non-int values.
* N12. `touch_map` is keyed by stage (`ladder`, `fresh`, `reference`), not per rung/probe as CCH-01 says; acceptable but note it.

## Item-by-item answers to the brief

1. **Status model.** Every `proven` setter was traced (`classify_cache`, `read_value`, `_basis`, `value`, `bps_basis`, `write_time_proof`). Holes: B2 (unsteady cold probe), B1 (burst under-count), B3/B4 (write value and inputs), B6 (cached IOPS in model), S1/S2 (QoS inheritance), S4 (unsteady depth). `no_cache_observed` is never proven (verified). `check` exit semantics are scoped per requested section but a missing/empty section yields 0 (B5).
2. **Estimators.** E-cold: first, confined, disjoint QD1 half (verified), but state is dropped (B2). E2: histogram split correct (hand-checked), adjacency correct, timestamps not recorded (S14), not isolated from the required stage (S5). E1: never raises, never decides. Write proof: arithmetic wrong for burst (B1), proven quantity differs from the capped one (B3). Metering: both tests required (verified).
3. **Derivations.** R1..R6 and the QoS oracles reproduce by hand (list above); DER-08's only cached_tier consumer is `qos_max`, but B6 shows a cache-contaminated figure reaching the model by the S6 read cells; S6/S7 list model/derivation weaknesses.
4. **FakeFio content model.** Real simulation (verified). `two_tier` fixture is a precedence test with a literal cold probe (S15); `fill_history_inversion` exercises placement dynamically; `cap <= R_cold` is true by `min()`.
5. **Plausibility.** PLA-01..06, 10..13, 15, 16 wired; PLA-07/09/17/18 never run (B7); PLA-06 weak effect (S13); data-integrity failures do surface as `failed` + `last-failed` (verified for `IntegrityError`, PLA-15, PLA-16) but only on `reduce_series` paths (N5).
6. **Planner and budgets.** 3280/4120 and the r1002 rung list verified. Budget truncation of whole stages is honest (`skipped: budget`, `limited-by-budget`, `unproven: budget_limited`); inside stages it is not enforced (S8) and the clock excludes S0/S1 (S8).
7. **Schema.** Required keys and unknown-major refusal OK; `cap_basis`/`reference` shapes diverge from section 18 (N1); newer minor accepted (B5/N11).

## Required before READY

B1..B7 fixed with run-level controlled-wrong oracles (not only pure-function ones), S1, S3, S5 and S8 (first bullet) fixed or ruled, and a re-run of `test_sta_05_status_collapse_audit` extended with rows for: unsteady cold probe, missing fresh observation, unmeasured fresh, moved fresh-vs-rewrite reference, burst hidden by smoothing, absent/empty `check` section.

**NOT-READY**

---

# Re-review r2 @201903edf

Base reviewed: 30a847beb; head 201903edf (5 commits). Read-only. In-process, serial, `nice -n 19 ionice -c3` pytest: `tests/test_review_b.py` 55 passed; `test_chk, test_slo, test_sta, test_cch, test_pla, test_prf` 84 passed. Scratch scenarios were run from the scratchpad against `src/` without touching the tree.
Out of scope per the coordinator and not re-reviewed: B-S12 (the BPS-write time-proof decision), B-S15, B-S16, B-N1, and the unfixed NITs.

## Verdict: NOT-READY

One new BLOCKING finding (B5 is only half closed: the default `check` still certifies a document that lacks proposal sections) and one in-scope finding not closed (S13, acknowledged in the notes). Everything else is closed.

## Per-finding status

| Finding | Status | Evidence |
|---|---|---|
| B1 burst under-count | **closed** | `series.py:144-166`: raw signed integral, leading raw sample moves the knee. Re-measured: gstammtisch burst 3.971e9 (= true), 240/500/540 s unproven, 560/600 s proven; one-sample 5 GB/s burst -> `steady`, knee 2.0, burst 4.985e9, unproven at 240 s; noisy tail still gives 4.985e9. Residual NIT: only samples 0-1 can move the knee, so a 2-sample burst that *starts* at sample 1 is counted as one sample (4.99e9 of 9.97e9); bursts start at t=0 in practice. |
| B2 unsteady cold probe | **closed** | `ColdProbe.state` (`cache.py`), `classify_cache` -> `undetermined/cache_model_undetermined` unless `steady`/`flat`; `rate_limited` now judged on the final 60 s window; `test_rev_b_b2_*` run level. |
| B3 cap vs proof series | **closed** (see scrutiny a) | cap = `min(rewrite, fresh, obs_write_steady, obs_fresh_steady)`, inputs recorded, observation at QD32. |
| B4 write-basis inputs | **closed** (see scrutiny b, c) | missing fresh obs -> `short_observation`; unmeasured fresh -> `fresh_not_measured`/`budget_limited`; fvr WRITE-reference move -> `ceiling_moved`; `StageSkipped` records `skipped`, `fresh_vs_rewrite.reason` is the real cause. |
| B5 `check` | **NOT closed** | explicit `--section` paths fixed (unknown/absent section -> Refusal exit 2, empty -> absent, status whitelist, newer minor refused). The **default** path is not: see NEW-1. |
| B6 read-model keys | **closed** | `derive.r6_keys` requires `cache_ratio`, applied to the fit-derived keys; `cache_residual_possible` when the read cache is unproven; unsteady cells leave fit and metering (`shape.certified`). |
| B7 PLA-07/09/17/18 | **closed** | `Calibrator.bytes_checks` runs in `assemble` before `apply_effects`; per-direction verdicts merged by `worst_check`; `not_applicable` when the closing S6 reference is missing; PLA-06/-13 run-level tests. |
| S1 QoS max inheritance | **closed** | `inherited` budget_limited/ceiling_moved from `cap_basis.read`; `best_read_rung` over steady rungs only, `None` -> `absent: basis_missing`. |
| S2 wlat/rlat | **closed** | inherit `cap_basis[dir].reasons`. |
| S3 stale S6 reference | **closed** | `r3s` taken at S6 start; reference 150 -> 180 s. |
| S4 unsteady depth rung | **closed** | `depth_unsteady` voids `qd90`. |
| S5 optional estimator errors | **closed** | `HistError` is a `SeriesError`; E2 wrapped in `self.stage`; `modes()` with no peak raises `HistError` (N3). |
| S6 worst window | **closed** | `worst_bps = min(basis, *windows, hist)`. |
| S7 TypeError | **closed** | absent iops basis tested before `min`. |
| S8 matrix/budget | **closed** | rejected knob = skipped candidate (S5 raises `SeriesError` if the native size cannot be set); per-cell `fits()` in S4/S6/S7/knob plan; budget starts at `Runner.started`; matrix inherits `short_observation`. |
| S9 dry-run rate | **closed** | `res.check` not stale/corrupt and `target.path == cfg.target`. |
| S10 foreign history | **closed** | `ceilings.rotate_foreign` before `assemble`. |
| S11 flat probe vs E2 | **closed** | E2 tolerance applies to `rate_cap`. |
| S13 PLA-06 exclusion | **not closed** | the listing of every failing rung is done, but `pla_06` effects are still `("caps","absent","stage_failed")` only (`plausibility.py:98`, unchanged) and the faked rung still feeds `best_read_rung`, `cached_ref` and the cliff. The notes admit this under "Not done", but the finding table row ("`iocost_qos` degraded by the run-level test") overstates it: the test checks `caps.RIOPS` only. Fix: add `("iocost_qos","absent","stage_failed")` to the PLA-06 effects, or exclude failing rungs from `best_read_rung`/`cached_ref`/cliff. |
| S14 E2 timestamps | **closed** | `E2.as_dict` carries `qd32_start`, `qd1_start`. |
| N3, N5, N7, N10, N11 | **closed** | `modes` no-peak -> `HistError`; non-finite fio log value -> `IntegrityError` at parse; `drain_rate` < 2 samples -> `SeriesError`; windows < 8 points not assessed; minor outside 0..own refused (int check). |

## New BLOCKING

### NEW-1. Default `check` certifies a document that lacks proposal sections (B5 residual)
`result.py:240-261`. When no `--section` is given, `asked = tuple(n for n in SECTIONS if n in group)`: the set of sections is taken from what the file carries, so a missing section is never examined. Reproduced in-process on a real calibrated result.json (scratch `chk.py`):

| result.json `proposals` | state / exit |
|---|---|
| intact | `current_unproven` / 7 |
| `iocost_model`, `iocost_qos`, `writeback` removed | **`current` / 0**, sections `{caps}` only |
| `proposals: {}` | **`current` / 0**, sections `{}` |
| `proposals: {"notes": []}` | **`current` / 0** |
| `caps: {"dev_aggregate": {}}` (non-empty top level, no leaf) | section summary `(0,0,0)`, no flag; with every other section proven this is exit 0 |

Failure scenario: a truncated or hand-edited result.json (or an interrupted writer on a host without atomic rename) leaves `proposals` empty; `iobench check` exits 0 and the wizard, which applies "benchmark when `check == 0`", applies nothing it can see: the precedence chain `benchmark > configured > kernel` is satisfied by a file with no benchmark in it. This is estate anti-pattern 2 (absence for emptiness), the very defect B5 was filed for, left open on the path every caller uses (`matrix.require_calibration` and `_run` pass no sections).
Fix: derive `asked` from the schema, not the file. For `result.json` require all four sections (`writeback`, `caps`, `iocost_model`, `iocost_qos`; CHK-03 guaranteed keys), for `calibration.json` the three it carries; a missing guaranteed section -> `corrupt/damaged_proposals` (exit 5). Require `proven + len(unproven) + len(absent) >= 1` per section and for `caps` require the four keys `RIOPS, WIOPS, RBPS, WBPS`. Oracles: the four rows above.

## Scrutiny items

### (a) B3: QD32 observation and the bounded cap
* Does the cap still mean what DESIGN-GUIDE says? Yes. The cap is a QD32 4 KiB random write rate at the largest working set (CAL-03/-07). The observation now runs at that depth on the same extent classes (`[0, ws)` and the fresh extent), so the proven series and the capped quantity measure the same thing, and the cap can only go down (`min`). DESIGN-GUIDE lines 262-264, SPEC CAL-05/-07 and CONSUMERS 135 agree with the code. Scenario `test_rev_b_b3_a_burst_that_outlives_the_30s_runs_does_not_set_the_cap` reproduces my 60 s-burst scenario and asserts `value == min(inputs) ~ L`. QD1/QD8 depth runs (R2) are untouched.
* Can cap and proof still come from different series? Only in the safe direction: the cap is the minimum over rewrite/fresh 30 s runs and both observation steadies, and each observation series carries its own proof, so every contributor to the minimum that is lower than the proven figure is a *lower* bound and cannot overstate. The cap is never above a proven series. Residuals (not blocking): (i) the fresh observation is 120 s and CCH-07 requires `observation_s - knee_s >= 120`, i.e. zero margin; real fio logs stamp the last sample at 119.9xx s, so on hardware the fresh half can never reach `proven` (FakeFio stamps exactly 120). Since B4 now makes the fresh proof mandatory, this turns a boundary quirk into a reachability problem for `proven` on every device that has a fresh half. Recommend 135 s (and the allotment +15 s) or a documented tolerance, and an acceptance-probe check in IOB-ACC-01. (ii) On a device with no fresh penalty, 120 s x ~70k IOPS x 4 KiB rewrites the 8 GiB fresh extent more than once, so later seconds are not "fresh"; the cap stays safe (`min`), but the series can read `unsteady` and give a needless `short_observation`.

### (b) Direction-aware movement: can it hide a real move? Yes, in a constructible case (SHOULD, not blocking)
Why the change was needed is confirmed: on a **healthy** FakeFio run the fresh-vs-rewrite group reports `moved_by_dir = {read: True, write: False}` (the read reference follows the read cache content). With the old `any()` every healthy run was `ceiling_moved`.
Scenario built (scratch `scen_b2.py`, FakeFio): a provider throttle on a shared budget starts at the fresh-vs-rewrite stage; the read QD8 reference sees it (`read.iops_cap=5000`); writes at QD32 are capped at 30k (`write.iops_cap=30000`) but the 15 s QD8 write reference is absorbed (write-back credit, simulated by pinning the write reference to its earlier value). Result: `moved_by_dir.fresh_vs_rewrite = {read: True, write: False}`, `cap_basis.write = {value 29655, status: proven, reasons: []}`, `caps.WIOPS` proven, PLA-13 pass, while the ladder's write rung was 72075. The comparison group straddles a ceiling move the run itself observed, and nothing on the write cap says so. (The old code would have flagged it, at the price of flagging every healthy run.)
The residual is bounded: the cap is the post-throttle figure, so it is conservative here; the harm is a silent mixed regime in `inputs` (ladder-derived QD90/R2 pre-move, cap post-move) and a lost warning.
Fix (either): record `read_reference_moved` in `limits[]`/PLA info whenever a direction other than the compared one moved and no cache-content explanation exists (the cache state is not `no_cache_observed`/`rate_cap` and the read reference's working set was evicted/allocated by the group's own writes), or add a QD32 write reference cell (15 s) so the write reference is taken at the depth of the comparison. Also note the asymmetry: the write basis still takes `ceiling_moved` from `moved["ladder"]` and BPS from `moved["shape"]` with `any()` (`calibrate.py` extras), while the fvr pair is per-direction. Make the rule one rule.

### (c) `fresh_not_measured`: collapse audit
Reached only via `if not self.quick and self.fvr is None: reasons.append(fresh_gap_reason())` inside the branch where the write rung is steady. Conditions that reach it (each is real absence of a fresh measurement): no fresh extent; discard/hole-punch refused (`StageSkipped`, recorded `skipped: <why>`); stage failed on an unusable series (`failed: ...`, `fresh_vs_rewrite.reason = stage_failed`); any other skip. A budget skip maps to `budget_limited` instead. All of these genuinely mean "the fresh penalty is not in this cap", and `fresh_vs_rewrite.reason` separates the causes, so there is no false certification in this direction.
Reverse direction (conditions that should reach it but do not): (1) quick profile: `quick_profile` instead, correct; (2) `fvr` measured but the fresh half not counted (file target on a mount without `discard`, `fresh_in_basis` false): no reason is added and the cap is `proven` although a measured fresh penalty (`fresh_fs_alloc`) was excluded by design (TGT-08); it is visible only in `limits[] += fresh_semantics_filesystem` and `fresh_vs_rewrite.penalty` (NIT, documented ruling); (3) the write rung `unsteady` -> `absent: basis_unsteady`, the reason is not added, correct. Also note the `fresh_not_measured` vocabulary count: `test_sta_02` and SPEC/CONSUMERS list it; the audit row exists in `test_rev_b_b4_2`.

### (d) Allotment 3310 / 4150: docs agree
Verified by grep over `libraries/iobench`: SPEC section 15 table (reference cells 180, total 3310, "290 s slack"), SPEC thorough row (4150), IOB-SLO-05 (3310 / 4150), the "thorough adds 840 s" sentence (780 + 60, unchanged and still correct), DESIGN-GUIDE lines 243 and 276, IMPLEMENTATION-NOTES, `plan.py` (reference 180), and `test_slo`/`test_prf` all say 3310/4150; no 3280/4120/320 s slack remains. Arithmetic: 3280 + 30 = 3310; 4120 + 30 = 4150 <= 4500 (350 s slack); quick unchanged at 670 (no `r3s` in quick). Minor: `dryrun.calibrate_plan` lists one reference pair under a 180 s allotment (pre-existing under-listing, NIT); IMPLEMENTATION-NOTES line 94 says "no SPEC change to the cap's definition" while SPEC CAL-07 was edited (the notes elsewhere say so; wording only).

### (e) `check` exit codes: conditions that collapse into each, both directions
| Exit / state | Conditions that reach it | Wrongly reached (collapse into it) | Wrongly *not* reached |
|---|---|---|---|
| 0 `current` | identity matches, every asked section has >= 1 value and all proven | **NEW-1**: a document missing sections, `proposals: {}`, or a section whose only child is empty | `--require-strong-identity` on a weak identity correctly demotes to 7 |
| 7 `current_unproven` | any asked section has an unproven or absent value; an empty section is reported as `absent: stage_skipped` | an emptied/damaged section is labelled `stage_skipped` (a lie: it is damage, belongs in 5); unproven values only from `identity_weak` | none found |
| 3 `stale` | devno/partuuid/size/non-null model/serial/wwn/file tuple differ | target vanished or `load_target` refuses, and `OSError` while reading sysfs, all map to stale via `live_identity` (N9, unfixed, out of scope) | a changed `workdir_fs`/scheduler is only a warning, per spec |
| 4 `absent` | no result (or calibration) file and no `*.last-failed.json` | `host.exists` false for an unreadable parent (EACCES) reads as absent rather than 5 | none |
| 5 `corrupt` | invalid JSON, not a dict, missing required top-level key | `schema_unsupported` now covers unknown major, newer minor and a non-int/negative minor (SPEC table text was updated to say so) | nested damage outside the section summary (`doc["identity"]["identity_strength"]`, `doc["quality"]["limits"]`, `run.started`) still raises `KeyError` (N4, unfixed) |
| 5 `calibration_missing` | result's `calibration_id` has no match | calibration.json absent, corrupt, unsupported, **or a newer valid calibration whose id differs** (a superseded result reads as "missing") | none |
| 5 `damaged_proposals` | `ValueError/KeyError/TypeError/AttributeError/IndexError` inside the section loop | the `except` is wide, so a programming error in `section_summary` also reads as a damaged file | an emptied section is *not* damaged (goes to 7, see above) |
| 6 `aborted_only` | result absent and `result.last-failed.json` exists | any file of that name, even empty or unreadable, counts; a good calibration may exist | none |
| 2 (Refusal) | unknown section name, or a section the document does not carry (`--section`) | none | the same absence is not refused on the default path (NEW-1) |
Fix list for (e): NEW-1; label an emptied section `damaged_proposals` (5), not `absent: stage_skipped`; narrow the `except` to the exceptions a damaged file can raise and let programming errors surface; split `calibration_missing` into missing vs superseded in the message.

## Counts

* B1-B7: 6 closed, 1 not closed (B5, residual = NEW-1), 0 regressed.
* S1-S14 (S12 out of scope): 12 closed, 1 not closed (S13), 0 regressed. (S12 not assessed.)
* N3, N5, N7, N10, N11: 5 closed.
* New findings: **1 BLOCKING** (NEW-1), 2 SHOULD (direction-aware movement hides a cross-direction move: scenario above; fresh observation 120 s has zero margin against the 120 s proof rule), 6 NIT (raw burst only moves the knee for the first two samples; write extras use `moved["ladder"]` with `any()` while the fvr pair is per-direction; BPS write reasons fall back to `short_observation` when the write basis is absent (`calibrate.py` `write_reasons`); `damaged_proposals` catch is wide; `calibration_missing` conflates superseded; dry-run lists one reference pair under a 180 s allotment).

**NOT-READY**

---

# Re-review r3 @c7d28cea3

Delta reviewed: 201903edf..c7d28cea3 (4 commits), scope B. Read-only; in-process serial `nice -n 19 ionice -c3` pytest `test_slo`, `test_prf` 15 passed (the r2 run of `test_review_b`, `test_chk`, `test_sta`, `test_cch`, `test_pla` was on the previous head). Scratch scenarios were run against the worktree sources.

## Verdict: READY

No BLOCKING finding remains in scope B. NEW-1 and S13 are closed. Three SHOULD items and four NITs are recorded; none lets a value be labelled `proven` that its basis does not prove. I recommend the three SHOULD items (and the P4 decision in item 5) are scheduled before P4 evidence is read, not before the merge.

## Per-item answers

### 1. NEW-1: `check` from the schema (closed)
Re-ran my reproductions in-process on a calibrated result.json:

| document | r2 | r3 |
|---|---|---|
| three sections removed | `current` / 0 | `current_unproven` / 7 (`iocost_model`, `iocost_qos`: 6 x `absent`) |
| `proposals: {}` | `current` / 0 | 7 (every leaf `absent: missing_from_document`) |
| `{"notes": []}` | `current` / 0 | 7 |
| `caps.dev_aggregate: {}` | silent | 7 (4 x `missing_from_document`) |
| `caps` with other sections emptied | silent | 7 |
| one leaf (`RBPS`) removed | not seen | 7 (`caps (2,1,1)`) |
| section is a list | not seen | 5 `damaged_proposals` |
| `calibration.json` with `proposals: {}` | not seen | 7, three sections of absents |

Required set per kind (`proposals.py` `CALIBRATION_SECTIONS`, `REQUIRED_LEAVES`): `result.json` = 4 sections (writeback 4 leaves, caps 4, iocost_model 6, iocost_qos 6); `calibration.json` = 3 (no writeback); `--section writeback` on a calibration is a Refusal (exit 2). I checked the producers: `build_caps`, `build_model` (all branches: skipped, poor fit, absent basis, undetermined), `build_qos` and the calibration `writeback` and the `--knob` matrix `writeback` always emit every leaf, so a legitimately shortened run (quick profile, a skipped S6/S7, a budget cut, a knob run) still carries all leaves, as `absent`/`unproven`. No legitimate document is blocked (full `test_chk`/`test_sta` pass at r2; the quick-profile path produces all keys). Matrix and dry-run call `check` without sections, so they now see all required leaves of a calibration.

Collapse audit of the absent reasons and exit-5 reasons:
* `missing_from_document` (7) vs `stage_skipped`: the first is reached only when a required leaf is absent from the file; `stage_skipped` only from a producer that wrote it (skipped stage, writeback of a calibration-only result). No producer can emit `missing_from_document` (it is in the closed list but `check`-only), so the two cannot collapse in either direction. An emptied section no longer borrows `stage_skipped` (r2 NIT fixed).
* `calibration_missing` (5): the result names a calibration id but `calibration.json` is absent, unreadable, corrupt or an unsupported schema. `calibration_superseded` (5): a valid calibration with a different id. The split is right. Residual: `calibration_missing` still also covers "present but corrupt or unsupported" (the `except LoadError` swallows the cause). NIT: report the load reason.
* Residual (NIT): a truncated document whose leaves are missing is exit 7 (`current_unproven`), the same as an honest run with unproven values; it is never 0, so it is safe, but a damaged file reads as "unproven". Consider exit 5 `damaged_proposals` when *every* required leaf of a section is missing.

### 2. S13: dropped rungs (closed; one SHOULD)
`calibrate.py` `usable()` drops a PLA-06 failing rung before `self.reads`/`self.writes`, so `classify_rungs` (cliff, `cached_ref`), `best_read_rung`, E1, QD90 and the largest-rung logic never see it; listed in `ladder.excluded_rungs`; depth rungs mark `depth_unsteady`; `hit_ref` and the densification step skip a failed rung. Built two scenarios (forced PLA-06 failure, scratch `s13.py`):
* one mid rung failing: complete, 11 read rungs, the failing rung in `excluded_rungs`, caps `absent: stage_failed` (the PLA-06 effect), QoS `proven` from the remaining rungs: consistent.
* all but the first two read rungs failing: the run is `complete` with a **two-rung** ladder (`cached_ref` of two rungs, `largest` = 2 GiB), the cache state `undetermined`, no `limits[]` entry, QoS `max`/`rlat` `unproven` with `device_smaller_than_cache_margin`.
Only an *empty* direction fails (`RunFailed`, "no usable ladder rung"). So: **SHOULD R3-1**, fewer than `MIN_RUNGS` (3) usable rungs yields a complete calibration with a ladder SPEC CAL-03/-04 does not recognise; the budget path raises "fewer than three ladder rungs" for the same condition. Also, dropping the planned largest rung shifts `largest`, but E2 (run on the real largest rung) is combined with the smaller rung's `steady_iops` (`e2_estimate(largest["steady_iops"], ...)`), and `device_smaller_than_cache_margin`/`budget_limited` is not flagged. Fix: require >= 3 usable read rungs and >= 1 write rung else `RunFailed`/`absent: stage_failed`; set `limited-by-budget`-class flag (or void E2) when the planned largest rung was dropped. No overclaiming value results meanwhile: caps are absent by the PLA-06 effect, and QoS statuses derive from the remaining cache state.

### 3. 150 s fresh observation, 3340 / 4180 (closed)
`ladder.FRESH_OBS_S = 150` (no tolerance in the rule, as asked); planner `fresh-observation` 150. Arithmetic: 3310 + 30 = 3340 <= 3600 (260 s slack); 4150 + 30 = 4180 <= 4500 (320 s slack). All of SPEC section 15 table (fresh observation 150, total 3340, "260 s slack"), the thorough row (4180), IOB-SLO-05, IOB-CAL-05 (150 s), DESIGN-GUIDE (lines 109, 243, 276, 291), IMPLEMENTATION-NOTES, `test_slo`, `test_prf` agree; no 3310/4150 survives outside history rows. A fresh run proves only when its knee is < 30 s (`150 - knee >= 120`), which is correct and documented.

### 4. B-S12: is `short_observation` honest for the write BPS cap? Not quite (SHOULD R3-2)
The write BPS cap and the model keys derived from it (`wbps`, `wseqiops`, `wrandiops`) now carry `short_observation` always (`calibrate.py` `write_reasons`; `iocost_model.w*` and `caps.WBPS` verified unproven in a healthy run). The status is honest; the **reason is not actionable**: `short_observation` tells a consumer to observe longer, and the remedy it suggests (`--profile thorough`, 900 s write observation) changes nothing, because the unproven series is the 30 s sequential 1 MiB cell, which is never extended. The same string also appears in `limits` semantics for the IOPS time proof and `matrix` writeback inheritance (`short_observation` copies from `cap_basis.write.reasons` into R1/R3), so the matrix writeback values now inherit `short_observation` from a BPS-only cause that is not the IOPS proof's; check: the matrix inherits from `cap_basis.write.reasons` (IOPS basis) not the BPS one, so it is not contaminated, but the vocabulary is overloaded. Recommend a distinct closed reason (for example `sequential_not_time_proven`) so a consumer reads "no run can fix this yet (backlog IOB-BL-02)" instead of "observe longer". Documented in CONSUMERS and BACKLOG IOB-BL-02, so not blocking. Consequence to state in CONSUMERS: `check` exit 0 is unreachable for any request that includes `caps` or `iocost_model` (WBPS and all three write model keys are permanently unproven; read model keys are `metering_undetermined` in my FakeFio run); only `--section iocost_qos` can be 0. Exit 7 is the only possible answer for the default request, so the wizard's "check == 0" branch is dead until IOB-BL-02/-03; the explicit-accept branch carries everything.

### 5. B-S15: the two-tier outcome (SPEC-conformant; a P4 decision, not a defect)
`test_rev_c_s15_two_tier_cache_end_to_end` runs 40 GiB @ 92k inside 300 GiB @ 15k over a 9.8k backing store through the Calibrator. Observed: E-cold `miss-like`, `unimodal`, `R_cold ~ 9.8k`; E2 bimodal with `R_E2 < 0.5 R_cold`; state `undetermined`, `cache_model_undetermined`; cap <= 0.7 R_cold; write proof proven with the burst counted.
* **By the SPEC text this is the right outcome.** IOB-CCH-05 row: "cold probe unimodal miss-like, E2 bimodal and the ratio outside the tolerance -> `undetermined`"; IOB-CCH-03: "When both `R_cold` and `R_E2` exist the ratio must lie in [0.7, 1.43]; outside is `undetermined`"; STA-03 requires the E2 cross-check "when E2 is available". Only unimodal/overlapping/voided/missing E2 is "not a failure". The cap is not overstated (<= 0.7 R_cold), so the result is safe.
* **Why the disagreement is an artefact here, and what P4 should decide.** `R_E2 = steady(QD32) x (1 - h2)` equals the backing store's capacity only if the backing store is the bottleneck of the QD32 run. With a middle tier, QD32 is limited by tier 2 (15k) and the 13 % misses load the backing store at ~1.6k IOPS, so `R_E2` is a utilisation, not a capacity (I confirmed `cache.modes` folds the three tiers into a bimodal 241 us / 758 us pair, h2 = 0.87, silently dropping the 110 us mode, CCH-02 says nothing about > 2 modes). E-cold is exactly the measurement SPEC section 6 designates as primary ("the cold region is the earliest-written region; when the fill wrote more than the cache holds it is cold under every allocation policy"), and here it reads the correct cache-free rate. So SPEC section 6's rationale supports "proven"; CCH-03/-05's gate supports "undetermined". The two texts disagree for multi-tier devices.
* **Recommendation for P4:** keep the safe outcome for this release, and decide with real histograms: (a) E2 is applicable only when exactly two modes survive; with >= 3 modes record `E2.shape = "multimodal"` (no cross-check, like `overlapping`) and let E-cold prove, but add a `limits[]` entry; or (b) keep the gate and accept `undetermined` for every multi-tier device. If (a), the SPEC rows CCH-02/-03/-05 must change together, and the `r1002` histograms (P4 stores them) decide whether r1002 is two- or three-modal. P4's checklist should record the mode count.

### 6. `fresh_not_measured` on a file target without discard (SHOULD, naming)
`calibrate.py`: `if not self.quick and not fresh_counts: reasons.append(self.fresh_gap_reason())`. For a file target on a mount without `discard`, `fvr` is measured (`fresh_fs_alloc`) but excluded by design, and the reason is `fresh_not_measured`. Reachability: it is now reached from (a) no extent / no discard / stage failed / any skip (genuinely unmeasured), and (b) measured but excluded (TGT-08), and `budget_limited` for a budget skip. In direction (b) the *name* is false (it was measured) although the status is right, and `fresh_vs_rewrite` records `measured: true`, `label: fresh_fs_alloc`, `mount_discard: "false"`, so a consumer can tell them apart in the artefact but not from the reason. Practical effect: every file target on a default ext4 mount has a permanently unproven `WIOPS` (honest: its cap lacks the device-level fresh penalty). The test asserts `fresh_not_measured in reasons is (not discard)`. Recommend a separate reason or at least `detail` text; the CONSUMERS paragraph already explains it. No false certification in either direction (a proven cap cannot reach this branch).

### 7. SPEC 18.2 against the code (closed)
Extracted the 18.2 JSON example and the 18.1 example and diffed key paths against a real calibrated `calibration.json`/`result.json` in-process: **no key in the SPEC example is missing from the actual document** (`spec-only` empty for the calibration block); for the result block the only SPEC-only keys are the matrix-only ones (`evidence.sweep.*`, `evidence.read_cost.*`, `run.stages.S3..S5`), correct because the example is a matrix result. Code-only keys are documented (`proposals`, `stages`, `shape.metering_detail`, `ladder.excluded_rungs` at SPEC line 435, `reference.{cells,moved,moved_by_dir}`, `cap_basis.<dir>.value/inputs`, `shape.fit` `<dir>-<pattern>-rewrite`, E2 `qd32_start`/`qd1_start`, `touch_map` per stage); the new absent reason and `calibration_superseded` are in the STA-05 tables and CHK-01.

## Counts (delta r3)

* NEW-1: closed. S13: closed (plus the rung-count SHOULD below). Item 3: closed. Item 7: closed. Items 4, 5, 6: no defect against the SPEC text; one SHOULD each on naming/decision as below.
* New findings: **0 BLOCKING**, 3 SHOULD (R3-1 fewer than three usable rungs completes silently and E2 is combined with a shifted `largest`; R3-2 `short_observation` is not an actionable reason for the write BPS cap and makes `check` exit 0 unreachable for caps/model; R3-3 `fresh_not_measured` names a measured-but-excluded fresh half), 4 NIT (`calibration_missing` swallows the load cause; a truncated section reads as 7, not 5; `cache.modes` silently drops a third latency mode, specify CCH-02 for >= 3 modes; the SPEC `check` examples need an exit-7-only note for default requests), plus one P4 decision item (E2 applicability for multi-tier devices, item 5).

**READY**
