# iobench backlog (IOB-BL)

Items the IOB-03 P3 review wave and the later review waves deliberately did not do. Each names why it waits and what closes it.

* **IOB-BL-01 dry-run preflight report mode.** `--dry-run` is plan-only and says `preflight: not_run`. A report mode that runs the live-host preflight, the namespace check and the work-dir checks read-only and prints the refusal a real run would raise (exit 2) needs a SPEC change to IOB-PRF-01 and a regeneration of the reviewed CLI cases.
* **IOB-BL-02 sequential-series time proof for the write BPS cap.** The write BPS cap is `unproven: short_observation` because its proof (the 4 KiB random observation) is a different series from the sequential 1 MiB figure it caps. Closing it needs a longer (>= 150 s) sequential 1 MiB observation run and its allotment.
* **IOB-BL-03 `seq1m` ceiling history.** Nothing iobench writes populates `seq1m` plateaus (S6 sequential cells are 30 s, below the 60 s CEI-02 rule). Until a P4-derived history exists, `check` exit 0 for the BPS caps and the model keys needs `iobench ceilings add`; exit 7 is the honest answer meanwhile. See IOB-BL-18 (iogov is the natural writer).
* **IOB-BL-04 cross-direction movement.** A comparison group is judged by the reference of its own direction. A provider throttle that shows only on reads is not seen by a write cap (DESIGN-GUIDE "Known blind spot"). Add a QD32 write reference cell or record other-direction movement; P4 plausibility checklist item.
* **IOB-BL-05 unfixed review NITs.** A: N1 (`planned_volume` literal), N4-N8, N10 (marker with serial-only identity, `--expect-serial` scope, silent device/file flags, correlated PSI ticks, remedy order, unbounded `--knob` dirty_bytes candidates), N-r2-4/-5. B: N2 (densification alignment), N4 (nested damage in `check`), N6 (noise floor in `beats_ref`), N8, N9, N12; the raw-burst knee only moves for the first two samples. (N9 scratch cleanup is now IOB-BL-09.)
* **IOB-BL-06 review r3 SHOULDs, safety (review A).** N-r3-1..4 FIXED in wave 2026-10-27 (tests `tests/test_review_d.py`); the `systemd-run` shape was pinned by the pre-release review (`tests/test_review_e.py`); the probe NIT was fixed with IOB-BL-28 on 2026-11-08.
  * N-r3-1 (fixed): an abort now stops the probe unit (`systemctl stop`, allowlisted for `iobench-probe-*` only, then waits for `is-active` to leave, bounded 30 s; a still-active unit is a `unit_not_stopped` restore warning).
  * N-r3-2 (fixed): the post-`killpg` reap is bounded (`REAP_TIMEOUT_S`); a pipe-holding survivor no longer hangs the run.
  * N-r3-3 (fixed): `devio.assert_writer_bound` requires `--allow_mounted_write=0` for device fio argv (RealHost and FakeHost alike); it is a mounted-device guard. The marker's kernel `O_EXCL` open protects only marker creation. RealHost refuses later raw-device mutation until a writer can use a held claim.
  * N-r3-4 (fixed): an exact key allowlist (the keys `FioJob.argv()` emits, each once, `--ioengine=libaio` only); abbreviations, `--output`, `--trigger-cmd`, `--write_lat_log` and repeats are refused. The `systemd-run` allowlist is now the exact probe-unit shape (`--wait --unit=iobench-probe-* --slice=*.slice --collect -p RuntimeMaxSec=N python3 -c SCRIPT DIR OUT DURATION INTERVAL`), and an unbound fio writer may only name an `iobench-*` load file (see IOB-BL-06b).
  * NIT (fixed 2026-11-08, `885f80b40`): `require_fio_option` requires the exact option name followed by `:` in `fio --cmdhelp` output; a longer option name is not a match.
* **IOB-BL-06b review-r2 NITs.** R2-5 CLOSED 2026-10-27: `check_argv` (RealHost and FakeHost) refuses a fio argv with a write `--rw` through a plain `run`/`spawn` unless its `--filename` is an iobench-named load file, refuses `blkdiscard`/`fallocate -p` outside `discard_range`, and refuses `--rw=trim*`/`rw`/`randrw`. Open: the abort path after a second signal still waits up to 30 s in `wait_inflight_zero` and up to 30 s per unit in `stop_unit` (bounded, so at most about a minute of deferral).
* **IOB-BL-07 review r3 SHOULDs, estimators (review B).**
  * R3-1: a ladder with fewer than three usable rungs still completes without a `limits[]` entry. It should be flagged.
  * R3-2: the write BPS cap reason `short_observation` is not actionable (`--profile thorough` cannot fix it). Give it a distinct reason naming the missing sequential proof. Until then, `check` exit 0 is unreachable for any request including `caps` or `iocost_model`; see also BL-02.
  * R3-3: `fresh_not_measured` is a false name when the fresh half was measured but excluded by design (a file target without discard). The artefact still tells the cases apart through `measured`/`mount_discard`.
  * NIT: a truncated document reads as exit 7 rather than 5.
* **IOB-BL-08 E2 applicability on multi-tier devices (decide in P4).** SPEC CCH-03/05 (E2 disagreeing with a miss-like cold probe gives `undetermined`) conflicts with SPEC section 6 (E-cold is primary) when there are more than two tiers. `R_E2` equals backing capacity only when the backing store is the QD32 bottleneck, and `cache.modes` folds three tiers into two. Proposed rule: E2 cross-checks only when exactly two latency modes are found, otherwise the result is "multimodal, no cross-check". Decide from the real histograms of the acceptance run; the `test_rev_c_s15_*` fixture currently encodes the safe `undetermined`.

## Added by the pre-release review (2026-10-27)

* **IOB-BL-09 scratch directory cleanup. CLOSED 2026-10-28** (review round 3): `Runner.clear_scratch` removes the files iobench named in `/run/iobench/<run_id>/` and the directory when empty, after the restore, on every exit path (`test_rev_e_the_run_directory_is_removed_after_the_run`). Not journalled: a crash before `cleanup` leaves the directory on tmpfs, which the next boot clears.
* **IOB-BL-10 hosts without a container runtime.** The live preflight probes `docker ps`; on a host with no docker the probe is `probe_failed` (a missing binary is now a refusal, not a traceback) and `calibrate`/`matrix` cannot run without `--installer`. Needs a decision in the D-007 frame: an explicit flag that states "no container runtime on this host" (never a silent default; the refusal text would name it), or `--production-pattern` semantics that cover it.
* **IOB-BL-11 file target/work dir on a filesystem without one backing disk. FIXED 2026-11-04 (fix-iob-1), extended for btrfs 2026-11-07 (IB-VQ F1/R6 and fix round 3):** unknown and unsupported backing identities refuse clearly; a single-device btrfs filesystem resolves through its UUID member list and agrees between init, calibration, root/namespace checks and work-dir checks. Work-dir resolution uses the filesystem found at the work-dir path itself, including when that path is the mount point; multi-device or unreadable btrfs refuses in either case. Namespace matching retains raw mountinfo devnos and resolved devnos, including matches through `_touching`. Original finding: `Runner.s0` derived the target disk through `device_chain(fs_info(...).devno)`; anonymous mount devnos produced a traceback and could disagree with target init. Other work dirs without one block device still refuse as `workdir_disk_unknown`.
* **IOB-BL-12 abort guard for reclaim-driven stalls.** The abort rule watches the protected cgroup's `io.pressure` only. On the live host the stalls observed in monitoring are driven by memory/swap reclaim, not by the disk (iogov design notes, 2026-10-27), and the matrix's buffered 1 GiB writer dirties the page cache: the stall class actually seen would not fire the guard. Add `memory.pressure some avg10` of the same cgroup to the abort rule (same threshold or a second flag; `--abort-psi` names a percentage, so one flag for both is defensible), with FakeHost coverage. Until then the guard protects against IO contention only, and `--confirm-quiet-window` is the operator's protection against the reclaim class.
* **IOB-BL-13 dry-run argv is representative, not exact. FIXED 2026-11-04 (fix-iob-1):** `dryrun.matrix_plan` builds S3, S4 and S5 from `Matrixer.load_pair` and `Runner.job`, so file names, log prefixes and queue depth are the run's own; the names carry the plan's run id (a real run has its own id). `test_iob_bl_13_*`. Original finding: `dryrun.matrix_plan` prints load files named `iobench-load-w`/`-f` with log prefixes `/run/iobench/s3-w`, while the run names them `iobench-<run_id>-<tag>-w` under `/run/iobench/<run_id>/`. Either build the plan from `Matrixer.load_jobs` or say in IOB-PRF-01 that names carry the run id.
* **IOB-BL-14 `_ProcHandle` descriptors. FIXED 2026-11-04 (fix-iob-1):** `kill()` closes both temporary files after the bounded wait (`test_bl14_*`, fake Popen; host.py stays outside the coverage run). Original finding: after `kill()` without `wait()` (the abort path) the two `TemporaryFile` objects are closed only by garbage collection. Close them in `kill()` after the bounded wait. RealHost only; no unit coverage until IOB-ACC-01.
* **IOB-BL-15 host-agnostic docs pass.** README, CONSUMERS, this backlog, `restore.py` and the test estate no longer name hosts (2026-10-27; the cgroup in examples is `game-server.slice`). `cli.py` examples and `tests/cli_cases.py` still say `wings.slice` because `docs/cli-surface.json`, `docs/CLI-SPEC.md` and the 173 reviewed cases in `docs/cli-review.toml` are generated from them: change them together with a surface regeneration (cli-extended review workflow). SPEC section 15, section 20, IOB-CAL-01/-04/-08, IOB-EVD-01, the DESIGN-GUIDE evidence labels, IMPLEMENTATION-NOTES, `tests/fixtures/r1002_*.json`, `testing.R1002_*` and the test ids `test_slo_05_standard_fits_on_r1002_geometry`, `test_cch_08_recorded_r1002` still carry the evidence hosts as provenance. A full pass renames fixtures and test ids (the SPEC oracles name them), so do it together with the P4 data refresh, replacing the host names by storage-class labels ("the 400 GiB VM partition", "the 14 MiB/s capped host").
* **IOB-BL-19 `sweep.drain_rate` on a short series (found 2026-11-04 during IOB-R2-01 triage).** With 2 rates the tail slice is `rates[len - 3:]`, i.e. `rates[-1:]` (a negative start), so the "median of the tail" is the last rate only; the intent for fewer than three rates is all of them (`max(0, ...)`). Real runs have tens of samples (S3 is 90 s), so it is latent. Fix with `max(0, ...)` and a test with 2 and 3 rates; `test_sweep_drain_rate_*` pins only that two rates are accepted.
* **IOB-BL-16 `Matrixer.written_bytes` and the diskstats row. FIXED 2026-11-04 (fix-iob-1):** a missing row raises `RunFailed` (`test_bl16_*`). Original finding: the S3/S4 drain samples come from the `/proc/diskstats` row whose name equals `workdir.disk_name`; a row that is missing mid-run (a hot-unplugged device) ends the run with a `StopIteration` traceback. Turn it into `RunFailed` (the restore already runs on the BaseException path).

## Added by host-tuning WP0 / WP0b (2026-11-05; design `host-tuning-DESIGN.md` 2.1, 4.1.1, review B2)

* **IOB-BL-20 disk scope of a filesystem on dm / LVM / md.** A file target's disk is `device_chain(fs.devno).disk_name`; for a filesystem on an LV or an md array that is `dm-N` / `mdN`, which has no model, serial or wwn, so its scope is weak (size + host) and does not join the physical disk's history. BL-11 refuses filesystems with no single block device (overlay, multi-device, network) but not a dm device. Resolve through `slaves/` (one underlying disk: its scope; several: refuse, `target_fs_disk_unknown`) once a host with LVM data needs it. The installer profile's raw-partition measurement is currently unavailable because RealHost refuses raw mutation until a held-claim writer exists; this disk-scope issue matters for file-target measurement.
* **IOB-BL-21 two identical serial-less disks.** Two virtio disks with the same model and size and no serial have the same scope key on one host (the key carries the host, not the device). If the target is gone, `find_disk` requires the remembered whole-disk name, stored model and size, and a weak candidate; a non-null stored machine-id hash must match the live non-null hash, while a null stored hash binds no host and permits lookup after a chroot's first boot. It never selects by size alone. A target-path replacement with the same name, model, size and machine-id is still indistinguishable when the stored hash is known; with a null stored hash, an equal-name, equal-model, equal-size weak swap is also accepted because no host or device identifier was captured. A bus path or `by-path` field could split identical disks, at the price of a key that changes with PCI slot. host-tuning must read the stored `identity.disk_scope.key`, not recompute a separate devkey.
* **IOB-BL-22 veryquick has no closing reference.** The design table kept "1 pair, 8 s" of reference cells; a pair that detects a moved ceiling needs an opening and a closing pair (32 s) and the earlier plan was 173 of 180 s. veryquick therefore takes none (IOB-PRF-03). Add the pair only if a plausibility need shows up in the first real runs; the device-class band of the design (kIOPS / MB-s band) was not built either: nothing consumes it yet.
* **IOB-BL-23 the veryquick numbers are fake-host numbers.** The >20x warning and >100x failure bands of IOB-PLA-19, the 20 s fill, the 15 / 15 / 10 s cold probe and the 30 s series are chosen, not measured; the FakeHost cannot tell a real cache from the simulated one. IOB-ACC-01 (first RealHost contact) must run `veryquick` once on the VM disk and once on a disk with a known host cache and record: elapsed seconds against the corrected 201 s plan (within the 210 s budget), the ratio and verdict, and whether the hit-reference on the last GiB written (the tail a write cache holds) separates a cache smaller than the written extent. Until then `quality.plausibility` is advisory for hosts that matter. A host-tuning consumer must give veryquick its own state directory so it cannot replace a fuller result.
* **IOB-BL-24 `ceilings add` cannot name the measured partition.** Benchmark observations carry `devno` and `partuuid` (IOB-CEI-01); an observation added through the CLI (governor, probe) has no such fields. Add `--devno` / `--partuuid` only when a consumer needs the partition of a governor observation.
* **IOB-BL-25 cloned templates can share a weak disk-scope key.** A serial-less disk key includes the hash of `/etc/machine-id` when that id was usable at calibration time; cloning a template before first boot can copy the same machine-id to multiple hosts and collide. Current per-host state bounds the impact, but imported history or consumers comparing keys can conflate clones. Proposal: when the machine-id equals a known-template value, include a boot-id or first-boot marker in the weak scope; first identify an authoritative template value and stable marker lifecycle. Placeholder serials include QEMU's full indexed `QM\d{5}` family and the VirtualBox / `vdisk` generated formats; the machine-id uniqueness assumption still needs a stronger answer for clones.
* **IOB-BL-26 calibrate inferred cache thresholds with real observations.** The IOB-PLA-19 bands (>20x advisory, >100x failure or >20x with a hit-like cold probe) and the IOB-CCH-09 1.2x repeat-read speedup hint are FakeHost-tested but not host-calibrated. At IOB-ACC-01, record these observations on the designated test host and a disk with a known host cache, compare each hint with the cold-region result, and revise the thresholds only from that evidence. Until then, both checks remain descriptive/advisory and no cache hint supplies a proposal value.

* **IOB-BL-27 IB-VQ PLA-06 attribution and measurement. UPDATED 2026-11-07 (instrumented; cause remains a hypothesis):** retained `v1001/ib-vq/out-setB/state-standard` evidence places the single 0.006 PLA-06 failure on a depth rung, most likely QD1 or QD8 write on the btrfs file, not an S7 latency-curve point. The evidence has `excluded_rungs: []`, `qd90.read: 32`, `qd90.write: null`, and `idle_qd1_p99_us.write: 3.888`; QD1 mean `clat` near 3.5 us gives a clat-only ratio near 0.0056 at about 1.6k IOPS, with p99 of 3.888 us. Submission/slat time is a hypothesis; with blocking submission, the ratio at QD>1 may still be near `1/QD`. PLA-06 uses `lat_ns.mean`, or `slat_ns.mean + clat_ns.mean`, and schema minor 5 persists each rung's IOPS, QD, mean clat/slat/total latency, status and value in `quality.little_law_rungs`; calibration also exposes depth rows at `calibration.ladder.depth_rungs`. Its detail names each rung and measurements. Missing usable latency from a fio version expected to report it fails as a measurement defect; unknown versions produce an explicit not-applicable reason. Failed-check degradation is the union over all failing rungs. A failed depth rung invalidates only its direction's QD90; a failed main QD32 rung still degrades caps. The old JSON cannot distinguish QD1 from QD8; the next live record will retain the evidence needed to test the hypothesis. Oracles: `test_r2_cal_a_v1001_qd1_write_replay_uses_total_latency_and_produces_a_depth_verdict`, `test_r2_cal_pla_06_merges_degradation_from_every_failing_rung`, and `test_pla_06_replays_the_v1001_qd1_write_depth_rung_and_names_its_job`; design and evidence: `docs/SPEC.md` IOB-PLA-06 and `docs/DESIGN-GUIDE.md` 7.2.

## Added from the 2026-11-08 IB-VQ r1002 rerun, controller run and v1001 reinstall

* **IOB-BL-28 raw-device mode requires fio `exclusive_open`, which fio 3.39 (Debian 13) does not have, although the docs say fio >= 3.30. FIXED 2026-11-08 (`885f80b40`, iobench-bl-20261108):** On the reinstalled v1001 `iobench-install-runner --target /dev/vda5 ...` exited 1 in under a second with `Refusal: fio_option_unsupported: the installed fio does not document the option 'exclusive_open'` (`fio --cmdhelp=exclusive_open`: "No such command"; a job with `--exclusive_open=1`: "unrecognized option"). `devio.require_fio_option` (`devio.py:130`, called from `stages.py:242`) and the fixed job argv (`host.py:165`, `devio.py:75`, `devio.py:323`) hard-require it, and the 3.30 floor in README, SPEC and the refusal text (`stages.py:229`, `devio.py:138`) does not mention it. The debian-install-v2 `io_benchmark` step therefore always ends `warned` on Debian 13 (DIV2-BL-03). Two defects: the documented fio floor is wrong, and the exclusive open has no supported spelling on the distro's fio. Check what fio 3.39 offers for an exclusive open of a block device (for example a differently named option, or opening through `O_EXCL` by another route) and keep the protection; never run a device job without it. Then correct the floor (or state "fio >= X with option Y") in README, SPEC and the refusal text, and make `--cmdhelp` detection exact rather than a prefix match (see the open NIT under IOB-BL-06). Close when a Debian 13 VM run reaches the raw stage, or refuses with a text naming the real minimum. Source: `reports/div2-reinstall-v1001-RESULT.md`, `reports/v1001-reinstall-EXEC-LOG.md` (17:25Z). **Resolution:** fio 3.30, 3.36 and 3.39 source/man output agree that the relevant current option is `allow_mounted_write` (default false); `exclusive_open` never existed and 3.39 rejects it. Every device job now pins `--allow_mounted_write=0` to make fio refuse destructive I/O to a target that appears mounted. iobench retains its kernel `O_EXCL` marker claim, mount/swap/holder checks and per-writer revalidation; fio's option covers the remaining mount race, not non-mount holders. The exact `--cmdhelp=allow_mounted_write` probe refuses an absent option with the installed fio version. README, CONSUMERS, DESIGN-GUIDE, SPEC, implementation notes, `pyproject.toml` and `cmru.toml` retain the fio >= 3.30 floor for the separate latency fields and the exact runtime probe for `allow_mounted_write`; the option is an additional mounted-device guard only. **Current resolution (2026-11-09, IOB-BL-28/31 review): CLOSED BY REFUSAL.** RealHost does not start raw-device fio writes or `blkdiscard`: it probes O_EXCL at the stage boundary and reports `device_in_use` if an opener exists, then refuses even on a free device with `raw_device_write_unsupported`. The marker's O_EXCL descriptor covers marker creation only; no claim is held across later stages, and this implementation does not guarantee safe raw-device mutation. A future raw writer must use the same held O_EXCL descriptor for its entire write interval. FakeHost keeps simulated raw writes for algorithm tests; these do not prove real-device safety. Regressions: `test_iob_bl_28_external_device_opener_after_init_blocks_the_writing_stage` and `test_iob_bl_28_realhost_never_launches_raw_fio_or_discard_under_an_exclusive_probe`.
* **IOB-BL-29 `standard` misses the 100,000 IOPS read cap in 2 of 3 runs, and one run says `fail` (IB-VQ F-R1).** r1002's virtual disk has a hard 100,000 reads/s limit (flat from QD16 to 512, latency x IOPS = in-flight within 2%, jitter +-0.03%). The three `standard` runs gave r1-s `cold_hit_like` -> fail (a rate cap misread as a cache hit), r2-s `rate_cap` -> pass, r3-s `undetermined`; IOB-PLA-05 reported `info: none` in all three although ladder rungs read 98.9-100.2k. Make the flat-line detector robust at a saturating depth (a ladder whose top rungs agree within about 0.1% and whose latency x IOPS tracks the in-flight count is a rate cap), and make the cold-probe classification refuse to call a flat-line `cold_hit_like` without the E2 cross-check. Close when the three retained states (`r1002/ib-vq-rerun/states`) classify the same way as a rate cap and PLA-05 reports it. Source: `reports/ib-vq-rerun-REPORT.md` F-R1.
* **IOB-BL-30 `veryquick` and `quick` cannot flag a rate cap (IB-VQ F-R2).** They report `rate_limited: not_assessed`. A short random-4k read probe at QD 1, 8 and 128 would expose it (IOPS flat from QD8 up, latency x IOPS = in-flight) for a few seconds of the budget. Decide whether the profile budgets (IOB-PRF-03; veryquick is 201 s) can afford it, or document that only `standard` can see a cap. Close when either the probe exists with a FakeHost test, or the profile table says so. Source: `reports/ib-vq-rerun-REPORT.md` F-R2.
* **IOB-BL-31 `target init --file` leaves about 40 GB of a 64 GiB file unwritten (IB-VQ F-R3). FIXED 2026-11-08 (`885f80b40`, iobench-bl-20261108):** The preallocated file's fill wrote about 26 GB; the rest are unwritten extents, and reading them returns zeros without device I/O (libaio cells over 60 GiB showed 204-245k IOPS, invalid). Document the behavior in README and DESIGN-GUIDE and add a "written bytes" status line to `target init` and `target status`, so an operator or a follow-up probe can restrict itself to written regions. Close when the status line exists with a test and the docs say it. Source: `reports/ib-vq-rerun-REPORT.md` F-R3. **Resolution:** `target init` and `target show` report `usable_bytes`, logical data-extent `written_bytes` and `unwritten_bytes` only on ext4, XFS, and single-device Btrfs. RealHost walks `SEEK_DATA`/`SEEK_HOLE` and clips to `[0, usable_bytes)`; the value is not physical allocation, nonzero content, or fio-written bytes, and query cost follows reported extent count (about two metadata seeks per extent). Other filesystem types report null extent fields with `written_bytes_status`; tmpfs remains refused. Raw devices and dry-run file init report null fields with status. Fake filesystem-type regressions cover all three admitted types and an unsupported type; they do not substitute for live ext4/XFS/Btrfs acceptance.
* **IOB-BL-32 IOB-PLA-11 (`shape_fit_poor`) still fails on `standard` in 3 of 3 r1002 runs.** The per-IO + per-byte linear fit is poor on that disk, so the model keys are `absent: stage_failed` and the QoS max is `unproven: shape_fit_poor`; no profile yields a proven derived value (3 proven `iocost_qos` constants in every run). IOB-PLA-06 now passes in all nine runs (0.998-1.0). Decide from the retained states whether the fit is poor because of the 100k rate cap (IOB-BL-29: a cap breaks the linear shape) or a real model limit; fit the model below the cap if so. Close when a rerun with the cap handled gives either a good fit or a documented reason. Source: `reports/ib-vq-rerun-REPORT.md` (earlier F4).
* **IOB-BL-33 controller write ceiling: about 125 MB/s sequential and 1.6k random 4 KiB write IOPS, and it is not io.cost.** On the controller host (gstammtisch) iobench measured seq write 117-125 MB/s and rand 4k write 1.6-3.0k IOPS (`standard` 0.26-0.35k) with io.cost disabled (`controller/ib-vq-rerun/out-nocost`), against r1002's 87-461 MB/s (cells differ by profile block size) and 3.5-4.4k; the earlier run with io.cost enabled (vrate 58-59%) gave 114.6 MB/s and 2.4k IOPS, so the governor does not explain the ceiling. Reads also differ (48-55k IOPS here against 100k). Investigate what bounds writes: per-LV or per-device limits (`dev.slice` `io.max` does not apply to the benchmark cgroup; `mq-deadline`, `nr_requests 16`, `max_sectors_kb 64` on vda), the btrfs LV target, writeback throttling while other agents share the disk (io PSI full avg10 up to 49 during the s4 move), or a provider write limit. Method: repeat the sweep on a block-device target with a sequential and a QD sweep for writes (read-only was the brief for r1002), with the limits audit output next to each run. Close when the ceiling is attributed (device, scheduler setting, cgroup, or platform) with the audit data, and `docs` record it as a known platform limit if it is the provider. Source: `reports/ib-vq-controller-REPORT.md`, `reports/ib-vq-rerun-REPORT.md` (comparison table, controller columns).

## IOB-REL-01 prerequisites (first release; recommended by the pre-release review)

1. Root `cmru.orchestration.toml`: `[orchestration.project.iobench] config = "libraries/iobench/cmru.toml" depends_on = ["cli-extended"]` (the wheel requires `cli-extended>=0.4.0`; `cli-extended-v0.5.0` is tagged, so the floor is satisfiable), `iobench` appended to `project_order` after `cli-extended` (any later position), then `cmru dependencies --write`. Not done by the review (an estate file, the controller's).
2. `CHANGES.md` exists (added 2026-10-27, `[Unreleased]` section); `cmru.toml` has no `changelog` override, so cmru folds its generated section into it. DONE 2026-10-28: prerequisite 1 (registration, `d06f10122`) and the `IOBENCH_RELEASE_NOTES` caveats of prerequisite 5.
3. No `iobench-v*` tag exists: the first release is `cmru release iobench --set-version 0.1.0` (never `--resume` on a first release).
4. The registered gate `run-gate r0-r1` must be green on the tree being released (tester-unified, not the devcontainer); the review wave changed `host.py`, `testing.py`, `matrix.py`, `stages.py`, `restore.py`, `devio.py` and the tests, so the 2026-10-27 PASS on the wave branch does not cover it.
5. The release notes (`IOBENCH_RELEASE_NOTES` in `cmru.toml`, and the CHANGES entry) must say: mechanics tested on a fake host only, `RealHost` never ran against a real device, fio or systemd (IOB-ACC-01 pending); requires root in the host's initial PID and mount namespaces, fio >= 3.30 for latency fields plus a build that documents `--allow_mounted_write`, a dedicated target prepared by `target init`; never persists a setting; `check` exit 7 is the expected answer for `caps`/`iocost_model` until a `seq1m` ceiling history exists (IOB-BL-03/-07); the abort guard covers IO pressure only (IOB-BL-12); the cross-direction blind spot (IOB-BL-04).

## Division of labour with iogovmon (iogovmon/, judged 2026-10-27)

* **iobench** = offline characterisation with fio on a dedicated target in a confirmed window: the fill, the ladder, the cold-region probe, the time-axis write proof, the cost-shape grid, the latency curve, the writeback sweep; output `calibration.json`/`result.json` with proof statuses. Minutes to an hour of saturating IO; root; never on a live host without `--confirm-quiet-window` and the abort guard.
* **iogov** = long-term, low-cost, unprivileged observation on the live host: raw read-only 4 KiB probes, a tiny ~6-hourly scratch-file write bench, passive PSI/vmstat/cgroup collectors, a recognizer for slow capability shifts, recommendations only. It never changes a setting and never saturates the disk.
* **What iobench stops doing / shares:** (1) the ceiling history is iogov's to populate (D-011, OI-2, IOB-BL-03): iogov's lanes (`r_cold` -> `read/rand4k`, `w_over_4k` -> `write/rand4k`, `w_fresh_qd1` -> `write/seq1m` plateaus of >= 60 s) map onto `iobench ceilings add --source probe`; the bridge (iogov `analyze --json` -> `ceilings add` lines, identity-bound by the same digest) is the one piece of integration work; (2) `--governor-histogram` (D-IOC-3) expects a latency histogram file that iogov does not produce yet; decide one format (fio's `clat_hist` bins, which `cache.aggregate_hist` already reads) before either side implements it; (3) `recalibrate_recommended` (IOB-MAT-04) and PLA-17 become meaningful only once (1) exists; (4) iobench's live facts to carry into P4: `io.latency` is not compiled into the host kernel and `io.prio.class` is unavailable (neither is used by iobench), iocost is enabled with `mq-deadline` and `nr_requests` 16 (inside R2's [8, 64] power-of-two range; a proposal of 16 reads as "confirmed", not "changed").
* **The old item "IOGOV adaptive-governor design (offered after P4)" is superseded** by iogov v1 (recommend-only recorder): there is no adaptive governor to design inside iobench; what remains is the bridge in (1)/(2). Removed from the resume list below.

## Resume here (state at the pre-release review, 2026-10-27)

* **Branches:** `iob-03-20261021` merged to main; `wave-iobench-20261027` (IOB-BL-06 N-r3-1..4, gate r0-r1 PASS, 637 tests) and on top of it the pre-release review branch (`tests/test_review_e.py`, 657 tests at 100 % branch coverage in the devcontainer, not a gate).
* **Review record:** `reviews/` next to this file (P1P2, P3 A and B with re-reviews), plus the pre-release review table in `IMPLEMENTATION-NOTES.md` ("Pre-release review 2026-10-27").
* **Next steps, in order:**
  1. Merge the review branch; `run-gate r0-r1` for libraries/iobench on the merged tree (prerequisite 4 above).
  2. IOB-REL-01: register (prerequisite 1), then `cmru release iobench --set-version 0.1.0` with the notes of prerequisite 5. A pre-P4 release is acceptable: it gives P4 a pinned wheel and sha256 to report against.
  3. P4 / IOB-ACC-01 on **the designated test host only, never a production host**. Calibrate first, then the matrix.
     * Live checks: verify the exact `allow_mounted_write` probe; raw write/discard stages refuse before any fio/blkdiscard launch (existing ordinary opener -> `device_in_use`, free device -> `raw_device_write_unsupported`); the O_EXCL marker open refuses a mounted or held target; a `RuntimeMaxSec` overrun exits non-zero; a double SIGTERM ends a file-target punch-hole operation; the IOB-BL-04 plausibility item; IOB-BL-08 decided from real histograms; the work dir never holds more than one cell's load files (IOB-TGT-05). Do not run a destructive raw-device acceptance job under the current implementation.
  4. Plausibility review of the acceptance-run numbers.
  5. The iogov bridge (division of labour, item 1), then IOB-BL-12.

## IOB-R2-01 — native R2 on r1002 (2026-11-02/03): 318 survivors, 5 budget-exceeded; triage plan

**Status:** OPEN (triage: groups A and C done, see below; B and the rest not started). The campaign is incomplete: planned 1,220, terminal 1,215, pending 5.

**Facts.**

- **Run.** First full native R2 for `libraries/iobench` (Run-Gate 24.3.0, assay 11, `jobs = "auto"`: 1 worker on the
  cold start, then 3).
  - Outcome: 897 killed, **318 survived**, 0 equivalent, 0 crashed, 0 hung, **5 `budget_exceeded`**.
  - Campaign wall time 15 h 02 min, including one Docker-restart interruption and the resume attempts.
- **Evidence:**
  - `evidence/iobench-r2-r1002/` of the release-program directory: `survivors-from-verdict.csv`,
    `budget-exceeded-from-verdict.csv`, `progress-iobench.jsonl`, the replay results;
  - the report `reports/IOBENCH-R2-R1002-2026-11-02.md`.
- **Candidate profile** (the scheduling data):
  - every candidate runs essentially the whole suite: elapsed p50 97 s, CPU p50 89 s, CPU-bound;
  - peak RSS p50 372 MiB, 84% within 350–400 MiB;
  - 116 candidates below 186 MiB, median 35 s: they fail fast;
  - 3 above 561 MiB, maximum 878 MiB.

**Survivors by file** (the operator mix and the largest line clusters, within 15 lines of each other, locate the
weak oracles):

| File | Candidates | Survived | Rate | Operators (survivors) | Largest clusters (lines, count) |
|---|---:|---:|---:|---|---|
| calibrate.py | 170 | 59 | 35% | compare-swap 31, boolop 18, bool-const 10 | 190–224 (8), 556–557 (8), 610–611 (8) |
| cache.py | 112 | 44 | 39% | compare-swap 32, bool-const 8, boolop 4 | 225–265 (19), 80–135 (14) |
| testing.py | 176 | 31 | 18% | compare-swap 16, bool-const 11, boolop 4 | 624–640 (8), 221–234 (5) |
| shape.py | 59 | 25 | 42% | compare-swap 18, bool-const 5, boolop 2 | 118–152 (17), 75–96 (6) |
| cli.py | 53 | 16 | 30% | falsy-swap 10, bool-const 4 | 234–268 (6), 319–350 (5) |
| guard.py | 110 | 16 | 15% | bool-const 7, boolop 5, compare-swap 4 | 213–241 (6) |
| series.py | 31 | 15 | 48% | compare-swap 13 | 112–132 (6), 151–162 (4) |
| dryrun.py | 20 | 14 | 70% | bool-const 6, compare-swap 6, boolop 2 | 71–89 (9) |
| restore.py | 75 | 13 | 17% | bool-const 6, compare-swap 4, boolop 3 | scattered |
| matrix.py | 53 | 10 | 19% | compare-swap 5, boolop 3, bool-const 2 | scattered |
| derive.py | 28 | 9 | 32% | compare-swap 9 | 69–87 (5) |
| plausibility.py | 31 | 9 | 29% | compare-swap 7, bool-const 2 | scattered |
| result.py | 60 | 9 | 15% | boolop 7, compare-swap 2 | 167–169 (4) |
| ceilings.py | 33 | 8 | 24% | compare-swap 5 | 128–139 (4) |
| devio, stages, sweep, ladder, proposals, plan, extents | 209 | 40 | 19% | mixed | small clusters |

**Triage status, group A (calibrate.py, cache.py, dryrun.py; fan-iob-a-20261103).** Every kill was checked by applying
the mutant by hand to a scratch copy of the package (AST rewrite at the survivor's line and operator) and running only
the new `tests/test_r2_*.py` files against it. The survivor rows are the R2 verdict CSV; where one CSV row maps to
several nodes on a line, the row is counted open if any node survived.

| File | Survivors | Killed | Equivalent / dead | Open | New tests |
|---|---:|---:|---:|---:|---|
| dryrun.py | 14 | 13 | 1 | 0 | `test_r2_dryrun.py` |
| cache.py | 44 | 37 | 4 | 3 | `test_r2_cache.py` |
| calibrate.py | 59 | 49 | 2 | 8 | `test_r2_calibrate.py`, `test_r2_calibrate_ladder.py` |

Equivalent or dead, with the evidence:

- dryrun.py:54 `run.target.disk_name or ""`. `Calibrator.disk_name` is read only by `Runner.supervise` and `execute`
  (stages.py 150, 233, 239), never by `make_plan`, `job` or anything `calibrate_plan` calls, so the assignment is dead
  in the plan path. Candidate for removal in a code step (not done here: tests only).
- cache.py:101 `len(peaks) > 1` to `>= 1`. With one peak, `masses[0]` is `sum(counts) / total` where
  `total = float(sum(counts))`, exactly 1.0, never below `MASS_MIN`; `weak` is empty, so the guard is redundant.
- cache.py:229 `a <= 0` to `a < 0`. `a = mean(1/y) - b * mean(x)` is strictly positive whenever `b < 0` (all rates
  positive), and `a <= 0` can only hold with `b > 0`, which the `b >= 0` operand already catches.
- cache.py:236 `0 < c_bytes` to `<=`, and cache.py:237 `c_bytes > 0` to `>=`. `c_bytes == 0` needs `b == 0`, which
  line 229 has already returned on; `nan` is false for both.
- calibrate.py:92 `budget_limited = False` initial value: unconditionally overwritten by `make_plan` (line 177)
  before any read.
- calibrate.py:534 `pla_18(..., False, ...)` to `True`: `pla_18` returns not-applicable on a `None` cell before it
  reads `moved`, and this branch is only taken when a cell is `None`.

Open (no deterministic oracle written; all are exact-float or exact-clock boundaries, or need a scripted ladder):

- cache.py:236, three comparisons: `r <= min(ys)`, `r2 >= 0.97`, `loo <= 0.15` at equality. Measure-zero on
  continuous quantities; an exhaustive dyadic search for `r == min(ys)` with a good fit found none.
- calibrate.py:190 (`now() < deadline`) and :198 (`runtime_s < runtime - 1`): fake-clock equality. The other `<` on
  each line is killed.
- calibrate.py:204 median of the fill tail exactly `0.5 * r_est`; :316 and :319 `CLIFF_RATIO * ref` equality;
  :416 `iops < 0.9 * offered` equality.
- calibrate.py:287: the ladder-cut `budget_limited = True` is observable only when `make_plan` did not already set it,
  which needs a slow stage with a full-size plan (time injection into the fake host).
- calibrate.py:314 `len(self.reads) > MIN_RUNGS` to `>=`: needs a ladder whose third rung falls below the cliff ratio;
  the fake cache models cannot produce that shape, so it wants a scripted `rung()`.
- Not verified one operator at a time: the two-line boolean chains at calibrate.py:556-557 and 610-611 are one `BoolOp`
  each in the AST (the CSV lists their operators on both lines). The tests give every rule its own failing decoy cell,
  so each single-operator flip is covered by reasoning, while the hand mutation flipped the whole chain.

**Triage status, group C (result, ceilings, devio, stages, sweep, ladder, proposals, plan, extents; fix-iob-1-20261104).**
Each kill was checked by applying the mutant to a scratch copy of the package (AST rewrite of the node at the survivor's
line, one node at a time where a line has several) and running only `tests/test_r2_iob1.py` against it. 58 survivor
rows: 56 killed, 2 equivalent, 0 open. The oracles are in `tests/test_r2_iob1.py`; the kinds are boundary at the exact
threshold (ceilings, ladder, sweep, result), frozen-dataclass and default-flag pins, and message content pins (devio).
(`stages.py` rows 218 and 233 are lines 227 and 242 after this package's edit.)

Equivalent:

- extents.py:32 `e <= start` to `e < start` and `s >= end` to `s > end`. A range that only touches the removed one falls
  through to `s < start` (appends `(s, start)`, the same range) or to `e > end` (appends `(end, e)`, the same range), so
  the result is identical.

**Triage plan**, in this order. Each step is a separate reviewed branch; tests only, unless a survivor shows dead code.

1. **Cluster first, not candidate by candidate.** The six largest clusters hold 74 survivors:
   - cache.py 225–265 and 80–135;
   - shape.py 118–152;
   - calibrate.py 190–224, 556–557 and 610–611.

   For each cluster, read the code block and its tests, then decide one of:
   - **(a) missing oracle:** add a boundary test that fails for the mutant;
   - **(b) equivalent:** prove equivalence by *simplifying the code* until the mutation point disappears. This is
     preferred to a waiver. It is reviewed;
   - **(c) dead or unreachable branch:** remove it.

   `compare-swap` dominates (170 of 318). Boundary tests (`<` against `<=` at exactly the threshold) are the expected
   fix for most of them.
2. **High-rate small files next:**
   - dryrun.py (70%, lines 71–89): plan-only output, likely unasserted fields;
   - series.py (48%);
   - shape.py (42%, the rest of it).
3. **cli.py `falsy-swap` (10):** an empty against a missing argument. Add CLI tests with empty-string values.
4. **The rest by file, by rate.**
5. **Rerun.** Use `run-gate r2` with the resume state, so only changed files' candidates and the 5 pending run. The
   rerun is cheap for unchanged files, because candidate ids stay stable while the file is unchanged.

**The five budget-exceeded candidates.** All are reproduced serially at 289–308 s, with no OOM. CPU almost equals wall
(295 s of 300 s), so they are **non-terminating loops created by the mutant**, not slow tests.

| Candidate | Location | Mutation | Tests completed before the budget |
|---|---|---|---:|
| `43207706…` | testing.py:596 | boolop And→Or | 3 |
| `6abf2348…` | testing.py:653 | boolop And→Or | 3 |
| `f3add887…` | testing.py:585 | compare-swap Eq→NotEq | 46 |
| `59336549…` | stages.py:141 | compare-swap Is→IsNot | 276 |
| `f83da146…` | sweep.py:61 | bool-const True→False | 678 |

- **Decision:** bound the loops in the fake host or test helpers rather than raise the candidate budget.
  - testing.py:585–653 is the fake host's wait or poll loops. Give them an iteration or fake-clock deadline that raises
    a test failure, so a non-terminating mutant becomes a fast kill.
  - For stages.py:141 and sweep.py:61, add a per-test timeout:
    - `pytest-timeout` if it is in the tester image, with a suite timeout of about 30 s, which is far above the
      longest real test;
    - otherwise the same bounded-loop rule in the code under test.
- **Acceptance:** each of the five, replayed with `--rejudge`, ends as `killed` within 60 s.

**Group B status (branch `fan-iob-b-20261103`, 2026-11-03; testing, shape, cli, guard, series, restore, matrix, derive, plausibility).**
All 144 survivors of these nine files are decided; 0 open. Tests: `tests/test_r2b_{testing,series,shape,cli,guard,restore,matrix,derive,plausibility}.py`
(116 tests; fast lane `run-gate r0-r1`: 870 passed, coverage 100 % branch). Kills were verified by applying each mutant by hand to a scratch copy of the
original source and running only the new test file (every "killed" below failed that file; nothing was rejudged by assay, which is not installed in the cockpit).

| File | Survived | Killed by a test | Mutation point removed (simplified / dead code) | Equivalent (argued) | Open |
|---|---:|---:|---:|---:|---:|
| testing.py | 31 | 22 | 5 (221 `i > 0`, 359, 640, 777, 266 dead `o_direct`) | 4 (96, 226, 351, 374) | 0 |
| shape.py | 25 | 23 | 0 | 2 (96, 128 `LtE->Lt`) | 0 |
| cli.py | 16 | 15 | 1 (324, `None if x is None else x` is `x`) | 0 | 0 |
| guard.py | 16 | 16 | 0 | 0 | 0 |
| series.py | 15 | 14 | 1 (186, `downsample` without the `<=` branch) | 0 | 0 |
| restore.py | 13 | 11 | 1 (370, dead `prior.locked = False`) | 1 (158) | 0 |
| matrix.py | 10 | 9 | 0 | 1 (181 `Lt->LtE`) | 0 |
| derive.py | 9 | 8 | 0 | 1 (71 `Gt->GtE`) | 0 |
| plausibility.py | 9 | 8 | 0 | 1 (27) | 0 |

Equivalence arguments (line numbers of the pre-change source):

- **Float boundary no float can hit** (shape.py 96 and 128, plausibility.py 27): the compared value is `q - 1` (or `1 - q`) for a quotient `q` of two floats,
  against 0.15 or 0.20, which are not dyadic. `q - 1` is exact for `q` in [0.5, 2), so equality needs `q = 1 + fl(0.15)` (or `1 - fl(0.2)`), which has more
  mantissa bits than a double holds (the low three mantissa bits of fl(0.15) are 011; those of fl(0.2) are 010). No input reaches the boundary, so `>` and `>=` agree.
  The other tolerance edges (cv 0.15/0.40/0.20, r2 0.9, residual 0.25, ratios 0.7/1.3/3/5/1.5/1.2) are reachable and are pinned exactly.
- **testing.py 96** (`order[b] <= 1e-12` after `order[b] -= d`): equality would need `a - fl(a - cap) == 1e-12` exactly with `a - cap > 1e-9`; the
  subtraction grid at that magnitude (about 2e-16 at a = 1) cannot represent 1e-12, so the remainder is never exactly the epsilon.
- **testing.py 226** (`w > 0` in `mode_profile`): the weights are differences of `phi` over a bin inside the +-4 sigma window; none is zero for any (latency, sigma) grid
  searched (8 latencies x 6 sigmas, every bin).
- **testing.py 351 and 374** (the fake's byte cap): at exact equality the original and the mutant both end the sample at the same `end_t` with `io_bytes == io_size`
  (the span `(io_size - io_bytes) / bps` is the whole span), so the outputs coincide; the `- 1e-6` tolerance in 351 cannot be hit exactly by the simulation.
- **restore.py 158**: the line above already refuses unless `protect_cgroup` and `abort_psi` are both given or both absent, so the `and` only narrows the types for `_validate_guards`; `or` behaves the same.
- **matrix.py 181** (`len(measured) < 3`): `measured` is always a prefix of the plan (the budget only shrinks), and `measured[-1] == REF2` holds only for the complete plan
  (6 or 8 cells); a 3-cell prefix is therefore refused by the `REF2` clause already. The `or` chain itself is pinned (4 measured cells now fail the stage).
- **derive.py 71** (`int(k) > cand`): the size equal to `cand` has just failed `v >= F`, so it is never in `meets` whichever way the boundary goes.

Other changes that came out of this group:

- **The five `budget_exceeded` candidates were non-terminating loops created by the mutant, not slow tests** (testing.py 585 `_resolve`, 596 and 653 the `while d and d not in dirs`
  parent walks, stages.py 141 `supervise` spinning on a finished handle, sweep.py 61 `quiet_gap_ended` never true). There is no `pytest-timeout` in tester-unified,
  and a bound inside the fake or the code under test would hide the loop, so `tests/conftest.py` now carries an autouse per-test CPU-time limit (delta review I1: it was a 30 s wall-clock alarm, which host load could turn into a test failure; it is now `ITIMER_PROF`/`SIGPROF`, so only CPU
  seconds of the test process count, 100 s = 10x the slowest measured test, 9.78 CPU s in a 946-test run; re-armed every second, raising a `BaseException` so a broad `except` cannot swallow it; `@pytest.mark.time_limit(cpu_seconds)` overrides, marker registered in `pytest.ini`).
  A sleeping or subprocess-waiting hang is deliberately not bounded by it (no CPU burns). The first replay under the old wall-clock limit: the five ended as killed (testing.py 585 in 18 s, 596 and 653 in 34 s, stages.py 141 in 7 s, sweep.py 61 in 128 s, the
  Replay with the CPU limit (2026-11-04, `-x`, host PSI some avg10 37-48, so wall includes contention): testing.py 585 23 s (RecursionError), 596 123 s, 653 154 s, stages.py 141 7 s,
  sweep.py 61 303 s (870 tests passed first, about 200 s under load, then the 100 s CPU limit); all five killed. The 100 s CPU limit costs a looping mutant 100 CPU s, so sweep.py 61 sits at the 300 s candidate budget under load: lower
  `time_limit` for the slowest legitimate test or reorder tests if it is again `budget_exceeded`. Old note: sweep.py 61's first failing test is test number 678 of 760, after about 95 s of passing tests plus the 30 s limit: it does not meet the 60 s acceptance by itself). A smaller
  limit would shave 15 s, not reach 60; the real lever is test selection or running the candidate's changed-file tests first (see "The bigger lever" below).
- `FakeDevice` no longer carries the unused `o_direct` attribute; `FakeHost._mount_for`, `_meminfo`, the fresh-penalty guard in `FakeFio.simulate`, `mode_profile` and series.py `downsample` lost the
  comparison that made an equivalent mutant (behaviour unchanged, covered by the old and the new tests). `restore.recover_previous` lost a `finally` that reset a local that is never read again.
- `test_bl06_n_r3_2_a_survivor_holding_the_pipe_cannot_hang_the_run` now signals readiness and reaping through FIFO/event handshakes, with no host-load-dependent wall-clock assertion. It reaps both the killed command and detached pipe holder. Verified in tester-unified: 20 sequential runs under `nice -n 10` with a parallel `nice -n 19` CPU burner; all passed and left no test-session children.

**Scheduling and profile hooks this lane will use** (cross-tool design `docs/design/RESOURCE-PROFILES.md`):

- **Profiles (assay B206).** The rerun records cgprofile profiles per candidate: exact numbers in cgroup mode at
  jobs=1, process-tree mode at jobs>1. Ids unchanged since the last campaign get per-candidate predictions (source 1).
- **Two-ended matching (assay B207, run-gate RG-148).**
  - The 3 high candidates run one at a time and early; the 116 short lows fill around them and at the tail.
  - `jobs=auto` is sized from the normal-class p95, 374 MiB, against B = 0.75 × the lane cap, which still gives N = 3
    on the 1,536 MiB cap.
  - The replay simulation shows that the running-RSS maximum at N=6 falls from 2,741 to 2,286 MiB, at the same
    makespan, within 0.05% of the lower bound.
  - For a full IOBench campaign the makespan gain is negligible, because durations are homogeneous. Rerun batches gain
    most.
- **Contamination (run-gate RG-144).** `budget_exceeded` candidates with contaminated profiles are listed for a serial
  `--rejudge` automatically. That is the manual procedure used for the five above.
- **Scaling run (RG-147).** IOBench is the M2 lane of the measurement plan: a 150-candidate stratified subset at
  N = 1, 2, 3 and 4 on r1002.
- **The bigger lever is outside scheduling.** Each candidate runs the whole suite (about 95 s). If the assay lane
  supports per-file test selection or coverage-guided selection, enabling it for iobench cuts the campaign far more
  than any ordering can. Check this before the rerun.

**Acceptance (of this item).**

- Every survivor cluster has a recorded decision (oracle added, equivalence proven by simplification, or code removed).
- The five timeouts end as fast kills.
- A resumed R2 completes the campaign: pending 0.
- The final survivor count, and every remaining survivor with its argued reason, are listed in this entry.

**Dependencies.**

- None for the triage itself.
- The rerun benefits from assay 12 (B206/B207) and run-gate 24.4 (RG-144, RG-148), but does not wait for them.
- r1002 is the runner (host-setup completion is not required for running R2).
