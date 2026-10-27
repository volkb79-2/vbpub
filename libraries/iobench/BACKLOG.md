# iobench backlog (IOB-BL)

Items the IOB-03 P3 review wave deliberately did not do. Each names why it waits and what closes it.

* **IOB-BL-01 dry-run preflight report mode.** `--dry-run` is plan-only and says `preflight: not_run`. A report mode that runs the live-host preflight, the namespace check and the work-dir checks read-only and prints the refusal a real run would raise (exit 2) needs a SPEC change to IOB-PRF-01 and a regeneration of the reviewed CLI cases.
* **IOB-BL-02 sequential-series time proof for the write BPS cap.** The write BPS cap is `unproven: short_observation` because its proof (the 4 KiB random observation) is a different series from the sequential 1 MiB figure it caps. Closing it needs a longer (>= 150 s) sequential 1 MiB observation run and its allotment.
* **IOB-BL-03 `seq1m` ceiling history.** Nothing iobench writes populates `seq1m` plateaus (S6 sequential cells are 30 s, below the 60 s CEI-02 rule). Until a P4-derived history exists, `check` exit 0 for the BPS caps and the model keys needs `iobench ceilings add`; exit 7 is the honest answer meanwhile.
* **IOB-BL-04 cross-direction movement.** A comparison group is judged by the reference of its own direction. A provider throttle that shows only on reads is not seen by a write cap (DESIGN-GUIDE "Known blind spot"). Add a QD32 write reference cell or record other-direction movement; P4 plausibility checklist item.
* **IOB-BL-05 unfixed review NITs.** A: N1 (`planned_volume` literal), N4-N10 (marker with serial-only identity, `--expect-serial` scope, silent device/file flags, correlated PSI ticks, remedy order, scratch cleanup, unbounded `--knob` dirty_bytes candidates), N-r2-4/-5. B: N2 (densification alignment), N4 (nested damage in `check`), N6 (noise floor in `beats_ref`), N8, N9, N12; the raw-burst knee only moves for the first two samples.
* **IOB-BL-06 review r3 SHOULDs, safety (review A).** N-r3-1..4 FIXED in wave 2026-10-27 (tests `tests/test_review_d.py`); the NIT and the `systemd-run` shape remain.
  * N-r3-1 (fixed): an abort now stops the probe unit (`systemctl stop`, allowlisted for `iobench-probe-*` only, then waits for `is-active` to leave, bounded 30 s; a still-active unit is a `unit_not_stopped` restore warning).
  * N-r3-2 (fixed): the post-`killpg` reap is bounded (`REAP_TIMEOUT_S`); a pipe-holding survivor no longer hangs the run.
  * N-r3-3 (fixed): `devio.assert_writer_bound` refuses a device writer without `--exclusive_open=1` (RealHost and FakeHost alike).
  * N-r3-4 (fixed for fio): an exact key allowlist (the keys `FioJob.argv()` emits, each once, `--ioengine=libaio` only); abbreviations, `--output`, `--trigger-cmd`, `--write_lat_log` and repeats are refused. Still open: the `systemd-run` allowlist only requires a `RuntimeMaxSec=` token (it does not pin `--unit=iobench-probe-*`/`--slice=`/`python3 -c`), and a non-target fio writer spawned with plain `spawn` (work-dir load files) has no `assert_writer_bound`.
  * NIT (open): the `--cmdhelp=exclusive_open` probe is a prefix match.
* **IOB-BL-06b review-r2 NITs (open).** R2-5: a raw `host.spawn` of a fio argv with a write `--rw` and no `--readonly` passes the allowlist; only `Runner.spawn` routes writers through `write_range`. The abort path after a second signal still waits up to 30 s in `wait_inflight_zero`.
* **IOB-BL-07 review r3 SHOULDs, estimators (review B).**
  * R3-1: a ladder with fewer than three usable rungs still completes without a `limits[]` entry. It should be flagged.
  * R3-2: the write BPS cap reason `short_observation` is not actionable (`--profile thorough` cannot fix it). Give it a distinct reason naming the missing sequential proof. Until then, `check` exit 0 is unreachable for any request including `caps` or `iocost_model`; see also BL-02.
  * R3-3: `fresh_not_measured` is a false name when the fresh half was measured but excluded by design (a file target without discard). The artefact still tells the cases apart through `measured`/`mount_discard`.
  * NIT: a truncated document reads as exit 7 rather than 5.
* **IOB-BL-08 E2 applicability on multi-tier devices (decide in P4).** SPEC CCH-03/05 (E2 disagreeing with a miss-like cold probe gives `undetermined`) conflicts with SPEC section 6 (E-cold is primary) when there are more than two tiers. `R_E2` equals backing capacity only when the backing store is the QD32 bottleneck, and `cache.modes` folds three tiers into two. Proposed rule: E2 cross-checks only when exactly two latency modes are found, otherwise the result is "multimodal, no cross-check". Decide from the real r1002 histograms; the `test_rev_c_s15_*` fixture currently encodes the safe `undetermined`.

## Resume here (state at wind-down, 2026-10-21)

* **Branch:** `iob-03-20261021` was merged to main with `--no-ff`. Reviews A and B were both READY at `c7d28cea3`. 609 in-process tests pass at 100 % branch coverage (devcontainer run, not a gate). The registered `r0-r1` gate was run on the merged tree on 2026-10-27 (PASS, 609 tests, 100 % branch coverage).
* **Review record:** see `reviews/` next to this file: the P1P2 doc/spec reviews, and P3 review A (safety) and B (estimators) with re-reviews r2 and r3.
* **Next steps, in order:**
  1. `run-gate r0-r1` for libraries/iobench.
  2. (done in wave 2026-10-27, gate r0-r1 PASS: IOB-BL-06 N-r3-1..4.) Remaining before any live run: none from BL-06.
  3. P4 / IOB-ACC-01 on **r1002 only, never nano1 or v1001**. Calibrate first, then the matrix.
     * Live checks: `--exclusive_open=1` refuses on a mounted scratch partition with EBUSY; a `RuntimeMaxSec` overrun exits non-zero; a double SIGTERM ends a running blkdiscard; the IOB-BL-04 plausibility item; IOB-BL-08 decided from the real histograms.
  4. Plausibility review of the r1002 numbers.
  5. The first wheel release (IOB-REL-01, wheel plus sha256).
  6. The optional IOGOV adaptive-governor design (offered to the operator after P4).
