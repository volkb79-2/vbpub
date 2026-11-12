# Changelog

All notable changes to iobench are recorded here. Entries marked `cmru: generated` are produced from the project-scoped release range before the release gate runs. The hand-written section carries what a consumer must know.

## [Unreleased]

### Added
- **IBX ladder-error study controller:** `studies/ibx/driver.py` now requires exact raw partition path/size/PARTUUID acknowledgement, refuses whole disks, mounted/swap-active targets, active exclusive claims, holders, and root/system devices, resolves btrfs members from sysfs, and supports a no-write `run --dry-run` rehearsal. Raw resume state records the size and PARTUUID. The guarded host acceptance commands and rollback are in [ACCEPTANCE.md](studies/ibx/ACCEPTANCE.md).
- **IBX schedule and evidence:** derive the base write cap from real fio job caps, run the t+6h standard rung only under the section 6.2 rule, report skipped optional work before it can exceed the meter, confine reads to fio-reported written extents, create the real fio log directory, and analyze quick/veryquick QD32 p99 latency proxies. See the [study procedure](studies/ibx/README.md), [design rationale](docs/DESIGN-GUIDE.md#ibx-ladder-error-study), and [consumer example](docs/CONSUMERS.md#ibx-host-study).

### Changed
- **IBX write budget:** cap the study at 500 GB decimal from one driver constant. The complete base schedule remains at its real 495.123688448 GB job-cap total; the part (c) repeat and both t+12h raw repeats are skipped because none fits in the remaining 4.876311552 GB. Optional file-standard sets report `skipped` unless their remaining cap and every unpaid required cell-set still ahead also fit, so an optional set cannot starve required work.

### Fixed
- Make fio stop `io_size`-bounded jobs at their byte goal while retaining `--runtime` as a ceiling; invalidate the 2026-11-08 fill-rate figures.
- Make the IBX study-day runbook stop on errors, restore previously active timers from an EXIT trap, and recheck the acknowledged target identity before formatting or wiping it.

<!-- cmru: release history -->

## [0.2.0] - 2026-11-10
<!-- cmru: generated -->
<!-- cmru: source-end=5cd92d24a468f00c1838f398683fdf798954c179 -->

### Added
- feat(iobench): disk-scope identity and history key, evidence classes, veryquick profile (host-tuning WP0, WP0b) (6a16e244b)

### Fixed
- fix(iobench): resolve live acceptance findings (49a2f04d6)
- fix(iobench): close review blockers IOB-BL-28/31 (8b90ddf8e)
- fix(iobench): support fio 3.39 and report written extents (885f80b40)
- fix(iobench): close IB-VQ review findings (3c70ec207)
- fix(iobench): address IB-VQ experiment defects (43b179b29)
- fix(iogovmon): track source install versions (66a4d1609)
- fix(iobench): address WP0 round-2 review findings (d45aacdfc)
- fix(iobench): address WP0 review findings (fa8affe5e)

### Changed
- Correct O_RDONLY flag assertion (8bb379bd8)
- Clarify raw-device O_EXCL holder semantics (728686175)
- iobench: retain ladder evidence and clean abort marks (7256f1c64)
- Fix iobench IB-VQ findings (2c3238181)
- iobench: fix capability review findings (3b4b91af6)
- iobench: record cache absorption evidence (c614ce7be)

### Documentation
- docs(iobench): scope parent-death cleanup guarantee (c47675218)
- docs(iobench): close IOB-BL-28 and IOB-BL-31 (b5bd54a9d)
- docs(iobench): file IOB-BL-28..33 (fio exclusive_open on fio 3.39, IB-VQ F-R1..F-R3, PLA-11, controller write ceiling) (ef5edb098)

### Testing
- test(iobench): assert public work-dir refusal output (af17cbaf0)
- test(iobench): align CLI refusal oracle and serial example (5b6838b4e)
- test(iobench): avoid exact IB-VQ replay ratio (41ef21a75)

<!-- cmru: pending release notes begin -->
### Fixed
- **Live acceptance cleanup:** asynchronous benchmark commands launched through `_ProcHandle` (`Host.spawn` and `Host.write_range`) use a direct parent-death supervisor and their own process group. Parent death or abort kills the whole group, waits for the direct command, and reaps adopted fork workers with a bounded wait. FakeHost abort coverage and a real local forked-process test cover this cleanup path; a repeat on the r1002 guest is still owed. Synchronous `Host.run` clients, including `systemd-run --wait`, are outside this guarantee; a killed transient-unit client leaves its unit running until explicit stop or `RuntimeMaxSec`.
- **virtio-blk serial identity:** read the whole-disk serial from `/sys/block/<disk>/serial`, the path reported by the live Debian 13 guest. The old `/sys/.../device/serial` location is not used as a fallback.
- **Target sizing:** device and file targets smaller than 1 GiB usable plus the reserved 1 MiB tail now refuse before marker writes or file allocation. Calibration and CLI validation use the same minimum.
- **Work-dir refusal:** a missing work dir now returns the normal refusal code and a clear setup remedy instead of an unexpected `statvfs` error. The directory must exist and be empty before a run; its free-space requirement is `max(4 GiB, 3 × planned cell volume)`.

### Added
- **Written-extent status (IOB-BL-31):** `target init --file` and `target show` report `usable_bytes`, `written_bytes`, `unwritten_bytes` and `written_bytes_status`. RealHost reports logical data-extent coverage via `SEEK_DATA`/`SEEK_HOLE` only on ext4, XFS and single-device Btrfs, clipped to the usable range. It is not physical allocation, nonzero content or fio-written bytes; query cost follows reported extent count. Other filesystems, raw devices and dry-run file init report null fields with a clear status; tmpfs remains refused.
- **Cache-absorption evidence (IOB-CAP-01/02, IOB-CCH-09):** calibration appends a disk-scoped `capability.jsonl` description row; `iobench capability [--since UTC] [--kind raw|file|probe] --json` reads it with per-kind counts/latest rows, and `check --json` shows the same per-kind summary. Malformed rows are skipped and counted; a newer schema is reported with its version and preserved. Standard/thorough can record delayed read-back and repeat-pass rates as advisory `quality.cache_state` hints; delayed reads compare against the same-QD, same-block-size QD1 cold reference, and an unrun cache stage is `not_measured`. These observations never supply io.cost values, and no recovery-time field is claimed.
- **IOB-PLA-19 in all profiles:** compare sequential read/write cells when both exist (same size, queue depth and region). Above 20x through 100x is inconclusive unless a hit-like cold probe corroborates cache service, which fails; above 100x fails without corroboration. Veryquick's cold class is always `not_assessed`, so its 20x–100x band is always inconclusive and cannot be corroborated. Thresholds remain inferred pending IOB-ACC-01 (IOB-BL-26).
- **Profile budgets and CLI:** standard cache evidence adds 60 planned seconds (3400 total) and thorough adds 90 seconds (4270 total) on the reference r1002 geometry. For other geometries, the stage is planned only when it fits the profile's remaining budget; otherwise the plan and both cells say `skipped:budget` and dry-run has no read jobs. `--cache-idle-delay-s` can change or disable the delay; quick and veryquick omit the stage.
- **`veryquick` calibration profile** (`calibrate --profile veryquick`, `IOB-PRF-03`): 201 s planned within a 210 s budget for install-time identity, sanity, and read/write IOPS. The retained runs took 188–189 s against the earlier 173 s plan; the new plan budgets the observed 16 s startup/reap allowance and a 12 s longer sequential-write cell. S0 26 s, S1 20 s, 25 s fill, E-cold 40 s, one 30 s write series, three 12 s cells and one 24 s sequential-write cell. **Every cap and model value is `unproven: veryquick_profile`**; the coarse model is not a fit. The eight IB-VQ runs did not repeatably classify the cold region, so the profile reports `cache_state: undetermined`, `class: not_assessed`, preserves the raw class as `diagnostic_class`, and always reports `cache_model_undetermined` for cache-dependent values. File targets require a positive `--size` aligned to 1 MiB; single-device btrfs file targets resolve to their backing disk and multi-device btrfs is refused for targets and work dirs.
- **`quality.plausibility = {verdict, cache_state}`** on every calibration (`pass`, `fail`, `inconclusive`); IOB-PLA-19 folds into every profile's verdict. Veryquick's cold class is never used to corroborate its 20x–100x PLA-19 band; that band remains inconclusive and only >100x fails. Ratio bands are inferred and await IOB-ACC-01 calibration (`IOB-BL-23`, `IOB-BL-26`).
- **Disk-scope identity and history key** (`IOB-CEI-06`, host-tuning review B2): `identity.disk_scope` (`model|serial|wwn|size_bytes` of the whole disk; a weak disk is bound to its host only when a machine-id hash was stored). QEMU `QM\d{5}`, `QEMU HARDDISK`, VirtualBox `VB` serials with two eight-hex groups, `vdisk` plus a decimal index, and common placeholders are weak. A weak disk lookup after target removal requires the remembered name, equal model and size, and a weak candidate; a known stored machine-id must match the live non-null id, while a null stored hash binds no host. Weak-to-strong changes are stale. Consumers use the key stored in the identity because chroot-created records can have a different live key after first boot.
- **Evidence classes** (`IOB-CEI-07`): every observation has a `kind` (`raw`, `file`, `probe`); writes are stable-confirmed by raw/file observations, reads by raw/probe observations, and raw rows alone supply model/cap values. `ceilings add --kind` validates source/kind pairs; `ceilings summary --kind` and `by_kind` report each class apart. `summary`, `check.history` and `recalibrate_recommended` use stability-eligible rows. Benchmark observations also record the measured partition's `devno` and `partuuid`.
- `target_changed` check warning: the measured partition or file is gone, formatted, replaced or resized.

### Changed
- **Result and calibration schema 1 minor 4:** records incomplete-run `partial` evidence and veryquick's `class: not_assessed`, raw `diagnostic_class`, and `intended_use`, alongside the complete calibration plan and optional cache-hint status/reference.
- **IB-VQ fix round 3, schema 1 minor 5:** calibration/result records retain every PLA-06 rung's job, QD, IOPS and mean `clat`, `slat` and total latency; the check detail includes these measurements, and failed-check effects are unioned across all failing rungs. Missing usable latency fields from a fio version expected to report them are a measurement defect. Quick's 18 s planning allowance is the observed maximum excess across all four runs on r1002 and v1001 (688 s versus 670 s); a 4% forecast tolerance remains within the 720 s hard budget.
- **Result validity is the disk, not the partition** (`IOB-RES-03`): `check` is `stale` when the whole-disk scope differs or the disk cannot be found. A weak scope becoming strong is stale; with no target path, a weak disk requires its remembered name, model, size and weak identity. A known stored machine-id must still match a live non-null id; a null stored hash binds no host. A throwaway partition deleted after the run, including after an installer chroot's first boot, does not make the result stale when its weak disk can still be identified. A work dir removed after the run is not `workdir_fs_changed`.
- **Breaking, schema minor 1 (nothing of iobench has run on a real host yet):** a calibration or result written before the disk scope (`schema_minor` 0) is `stale`, `ceilings add` refuses its identity (`identity_legacy`) and its history is moved aside by the next calibration. Observations require a `kind`; `ceilings add --source governor` requires `--kind` (a governor measures on a scratch file or a raw device, it has to say which).
- `ceilings summary` adds `by_kind`, and its aggregate (like the one in `show --ceilings`) counts only stability-eligible observations; a file target's history now shares the disk's key instead of being kept apart by the target kind (`IOB-TGT-09`).
- `FakeHost` lists `/sys/block` (whole disks) and symlinks in `listdir`; `standard_host` has an `/etc/machine-id`.

### Fixed
- **Raw-device mutation refused (IOB-BL-28):** RealHost probes `O_RDONLY|O_EXCL` at the raw write/discard boundary. On Linux, EBUSY identifies a kernel block-device holder conflict, such as a mounted filesystem or another exclusive holder; an ordinary non-exclusive opener does not make the probe return EBUSY. The stage maps the busy result to `device_in_use` and refuses even after a successful probe with `raw_device_write_unsupported`, before fio or `blkdiscard` launches, because neither can use the same held O_EXCL descriptor for the whole mutation interval. Marker creation uses O_EXCL only while writing the marker. `--allow_mounted_write=0` remains an additional mounted-device guard. fio >= 3.30 remains required for its latency fields, and startup checks the exact mounted-write option. Raw mutation is unavailable until a claimed writer exists.
- **IB-VQ target, planning and evidence fixes:** btrfs file init, calibration and work-dir checks resolve the same single backing disk from filesystem member facts; multi-device or unreadable btrfs member lists refuse, including when a work-dir path is itself the mount point. Namespace matching uses both raw and resolved device numbers. File sizes are checked at parse and plan time for positive MiB alignment. Failed calibration and matrix runs persist completed measurements as `partial.status: incomplete`. `limited-by-budget` now identifies time truncation; free-space and short-fio stops use `limited-by-space` and `limited-by-fio-short` so `cache_proof` stays conservative.
- **IB-VQ classification and timing:** veryquick cold classes report `not_assessed` with raw `diagnostic_class` retained and `cache_model_undetermined` on cache-dependent values; its install-time use is identity, sanity and read/write IOPS. Timing plans use independent IB-VQ observations: veryquick plans 201 s within 210 s; quick plans 688 s within 720 s. PLA-19 records unsteady cells and does not use a veryquick hit-like cold class as corroboration.
- **IB-VQ PLA-06 correction (IOB-BL-27):** the retained 0.006 failure is most likely from the QD1 or QD8 write depth rung on the btrfs file, not a rate-limited S7 point. Submission time is a hypothesis: the retained mean `clat` near 3.5 us and p99 of 3.888 us, at about 1.6k IOPS, give a QD1 clat-only Little's-law ratio near 0.0056; at QD>1, blocking submission could still produce a ratio near `1/QD`. PLA-06 uses total latency (`lat_ns.mean`, otherwise `slat_ns.mean + clat_ns.mean`), records per-rung latency evidence, and treats absent expected fio latency fields as a measurement defect. Failed depth rungs invalidate only that direction's QD90; failed main QD32 rungs retain their cap effects. The next live record can distinguish the hypothesis using its rung `slat` and total-latency means.
- **Capability history review:** non-object JSON headers are handled as invalid history; a capability row is finalized only after complete result records are durable, so an interrupted write cannot leave an orphan final row. `quality.warnings`, capability warning vocabulary and `R_cold_qd1_bps` are documented.
- Weak disk scopes no longer resolve by size alone; replaced weak disks and weak-to-strong changes are stale. Virtual placeholder serial patterns are weak, null stored machine-id hashes remain unbound across first boot, and identical cloned-template machine-ids remain tracked in IOB-BL-25.
- File/probe history can confirm stability only and cannot lower model/cap proposal values; `check.history` uses the same filtered stability rows as `ceilings summary`.
- `ceilings add` rejects impossible `--source`/`--kind` combinations. `veryquick` preserves existing fuller calibration/result files and requires a separate `--state-dir` when needed. Mismatched history is named `.superseded-<timestamp>`.
<!-- cmru: pending release notes end -->

## [0.1.1] - 2026-11-04
<!-- cmru: generated -->
<!-- cmru: source-end=fc3db7ddfda84e38e662bccd4da708645ee69b48 -->

### Added
- feat(run-gate): enable native R2 jobs (36d7b97b5)

### Fixed
- fix(iobench): per-test limit counts CPU time not wall-clock; procfs-unreadable child check warns (delta review I1-I4) (511fc0c12)
- fix(run-gate): address PR3 capacity review (5e1a2aae2)
- fix: repair merged R2 gate failures (80e3d81fd)
- fix: repair whole-source R2 start failures (5b042b60b)
- fix(assay): repair main's red assay tests: v16 fixture evidence, empty-shard verdict fixture, frozen schema digest, state-dir missing refusal, iobench lane config; fix doc anchors; document schema v2 of analyze campaign (154983976)

### Changed
- iobench: IOB-R2-01 group C oracles (result, ceilings, devio, stages, sweep, ladder, proposals, plan, extents); BL-11/13/14/16 docs; IOB-BL-19 (7be7bb993)
- iobench: IOB-BL-11/13/14/16 (file-target refusal, exact dry-run argv, close kill() temp files, diskstats RunFailed) (7df306657)
- Merge branch 'fan-iob-b-20261103' (6350a31b4)
- iobench: IOB-R2-01 group B status (per-file triage, equivalence arguments, budget-exceeded diagnosis) (7e84620be)
- iobench R2 (IOB-R2-01 B): plausibility threshold oracles (bd28b08c2)
- iobench R2 (IOB-R2-01 B): derive boundary oracles (785ed3484)
- iobench: IOB-R2-01 group A status (calibrate/cache/dryrun) and the budget and bytes-check oracles (7ebfc174e)
- iobench R2 (IOB-R2-01 B): matrix oracles (420f8dc3c)
- iobench R2 (IOB-R2-01 B): restore oracles; drop the dead prior.locked reset in recover_previous (8cb335c81)
- iobench R2 (IOB-R2-01 B): guard oracles (0b0476f40)
- iobench: calibration oracles for IOB-R2-01 (fill, ladder densification, analysis, document assembly) (ffe80b9b6)
- iobench R2 (IOB-R2-01 B): shape and cli oracles (a6a5f6f46)
- iobench R2 (IOB-R2-01 B): FakeHost and series oracles; per-test time limit turns non-terminating mutants into fast kills (f1309a5d9)
- iobench: cache estimator boundary oracles for IOB-R2-01 (37 of 44 cache.py survivors killed; 4 equivalent, 3 float-boundary open) (b425d1b8f)
- iobench: dry-run plan oracles for IOB-R2-01 (13 of 14 dryrun.py survivors killed; line 54 is dead in the plan path) (906eb4d2c)
- Add combined resource settings and IO sync delivery (da11e76b7)
- Fix iobench test child cleanup (fbecef180)
- Merge assay 10.0.0 candidate: R2-free provisional lane, CMRU release-version binding, review fixes (reasonix-reviewed) (f6ad91128)

### Documentation
- docs(iobench): IOB-R2-01 triage plan with scheduling and profile hooks (6b146cc91)
- docs(iobench): file IOB-R2-01 (first native R2: 318 survivors, 5 budget-exceeded) (df2e3ef3d)

### Testing
- test(assay): cover socket-vs-directory group mismatch in the observer gid guard; iobench lane enforces branch coverage (f372310c2)

<!-- cmru: pending release notes begin -->
### Changed
- Run the registered R2 lane through native Run-Gate Assay dispatch with `jobs = "auto"`; raise the Assay lane ceiling from the legacy `jobs = 1` alias to canonical `jobs_max = 4`.
- This command-to-native lane switch changes campaign storage: the old `.assay/mutation-state-r2` state directory and `.assay/progress-r2.jsonl` progress file are not reused. The native lane uses Run-Gate's durable state area under `.run-gate/` and `.assay/progress-iobench.jsonl`; a campaign already running with the old configuration does not carry over.

### Fixed
- `calibrate` with a file target on a filesystem with no single block device behind it (overlay, multi-device, network) refuses cleanly (`target_fs_disk_unknown`, exit 2) instead of a traceback (IOB-BL-11).
- `matrix` ends with a failed run, not a `StopIteration` traceback, when the work dir's `/proc/diskstats` row disappears mid-run (IOB-BL-16).
- `--dry-run` for `matrix` prints the argv a real run builds (file names, log prefixes, queue depth), built from the same job constructors; names carry the plan's run id (IOB-BL-13).
- The temporary stdout/stderr files of a killed fio are closed in `kill()` (IOB-BL-14).
- New oracles for the R2 survivors in result, ceilings, devio, stages, sweep, ladder, proposals, plan and extents (IOB-R2-01).
- Keep the Assay whole-target lane measurable: exclude the docstring-only package initializer, let pytest finish the coverage run after a failure, and reap both detached subprocesses created by the bounded-reaping test.
- Test infrastructure (`tests/conftest.py`, not shipped in the wheel): every test now runs under a CPU-time limit (`ITIMER_PROF`, 100 CPU seconds, 10x the slowest measured test, `@pytest.mark.time_limit(cpu_seconds)` overrides) so a mutant's non-terminating loop is killed while host load cannot fail a slow-but-correct test; the session registers as child subreaper to check for unreaped children, and degrades to a warning when `/proc/<pid>/task/<pid>/children` is unreadable.
<!-- cmru: pending release notes end -->

## [0.1.0] - 2026-10-28
<!-- cmru: generated -->
<!-- cmru: source-end=8cbb0352f075d37601143d79512f7a8b80d41b13 -->

### Fixed
- fix(iobench): init_file revokes only the scratch root it added, on every exit but success; a None/symlink stat after fallocate is path_unclean (fdd7b6a6a)
- fix(iobench): round-4 review lows: TOCTOU limit stated, target-init directory authorised only after the checks (revoked on refusal), pseudo filesystems refused, fio .N.log cleanup (43eaa540a)
- fix(iobench): review round 4 fold-ins: contained preallocate, scratch cleanup that only removes iobench's fio logs and never raises, typed fio --name (ac7938bd3)
- fix(iobench): review round 3: every non-fio argv pinned to its builder, fallocate only through Host.preallocate/discard_range, caps, run-dir cleanup, release-note caveats (9da4531f5)
- fix(iobench): review round 2: scratch-root containment for every argv path, the probe script pinned byte for byte, typed fio values (61b85b430)
- fix(iobench): pre-release review: Host-level argv bindings, per-cell load-file removal, guard-failure abort, preflight input refusals (7b485d7f8)
- fix(iobench): review r2 for IOB-BL-06: sweep repeat cells no longer put the cell name (/ * +) into the probe unit name; unknown unit state is not 'gone'; FakeHost validates unit names (caca5f042)
- fix(iobench): review r1 for IOB-BL-06: exact fio key allowlist, abort path never raises, ActiveState wait, plain probe-unit stop names (2982c1ff9)
- fix(iobench): IOB-BL-06 N-r3-1..4 abort stops the probe unit, bounded reap after killpg, exclusive device writers, tighter fio allowlist (b24167e69)

### Changed
- tests/docs: clarify init_file scratch-root revocation (9b7f442df)
- iobench: wind-down backlog (r3 SHOULDs, E2 multi-tier decision) and review record (8b0e5b33f)
- iobench: notes for the coordinator rulings (c7d28cea3)
- iobench: two-tier end-to-end test, SPEC 18 reconciled with code, docs for the rulings, backlog (53ae0533f)
- iobench: ruling 3 (write BPS unproven), NEW-1 schema-derived check, S13 exclusion, 150 s fresh observation, fresh exclusion reason (0a1f7d3b5)
- iobench: coordinator rulings 1-2, A-S1, A-S9, N2, N-r2-1..3 (2bfd8935a)
- iobench: review wave docs triad, notes, file-target link recheck, drain_rate guard (201903edf)
- iobench: review A should-fixes (holders, file targets, orphans, partial spawns, drain warning) (dc2a92ccb)
- iobench: review B tests (run-level oracles per finding), direction-aware reference movement (9f5cef56d)
- iobench: review B should-fixes (fresh reference before S6, budgets, QoS inheritance, matrix, history rotation) (4eb5f123c)
- iobench: review B estimator and derivation fixes (burst proof, cold state, write basis, check, model, PLA wiring) (795bfe774)
- iobench: review A safety fixes (journal ownership, coupled sysctls, containers, probe codes) (4c86fde8c)
- iobench: no_cache_observed is unproven (cache_not_excluded); corrected R-11 (30a847beb)
- iobench: coordinator rulings - wheel-only REL-01, CAL-01 numbers, SHP-04/PLA-11, no_cache_observed (3c6860c1c)
- iobench: reviewed CLI cases, audit test, notes, cleanup (6668d5342)
- iobench: 100% branch coverage, corner-case tests, CCH-08/EVD/DOC/GATE, CLI tests (859f6fd25)
- iobench: fixtures, EVD replays, docs-sync and gate tests, PLA-15 end-of-run identity check (265eafca2)
- iobench: CLI shell, packaging, generated CLI surface (review cases pending) (bc62742d4)
- iobench: audit, safety/arch/lat/cei/cch integration tests and fixes (44a06167a)
- iobench: check/show/result tests, implementation notes (8bd1b1777)
- iobench: matrix and file-target stage tests (eb7ec563f)
- iobench: dry-run planner, slo/prf stage tests, fixes (ac9a04f40)
- iobench: calibration and matrix runners (first end-to-end on FakeHost) (fc1d1af23)
- iobench: safety journal, ceilings history, result/check, plausibility, proposals status model (a5a45c021)
- iobench: sweep decisions and derivation rules (5d4397be4)
- iobench: ladder, shape and metering cores (91b570389)
- iobench: target guard, errors, file targets, work-dir rules (c57eb3d2b)
- iobench: cold probe record test (746607aa6)
- iobench: planner (allotments, rungs) and cache-state estimators (83c693fdd)
- iobench: foundations (series, extents, host protocol, devio, FakeHost/FakeFio with content-based cache) (63e022dc8)
- iobench: revision 4 after controller re-review R2-1..R2-4 (651a99ea4)
- iobench: revision 3 after controller review O-1..O-8 (31358d690)
- iobench: revision 2 of README, DESIGN-GUIDE, CONSUMERS, SPEC; assay lane (IOB-03 P1/P2 review fixes) (50492cbbb)
- iobench: README, DESIGN-GUIDE, CONSUMERS, SPEC and run-gate lane (IOB-03 P1/P2) (b9d1f2178)

<!-- cmru: pending release notes begin -->
First release candidate (IOB-03 P3 plus the review waves). At that point everything below was verified on the fake host (`FakeHost`/`FakeFio`, 100 % branch coverage over `src/iobench` except `host.py` and `__main__.py`, each excluded with a stated reason). The later 2026-11-09 r1002 QEMU/TCG live probe is partial mechanics evidence only; see the current Unreleased notes and IOB-ACC-01 status in `docs/SPEC.md`.

### Added
- The measurement core (calibration: fill, cold-region probe, ladder, fresh vs rewrite, time-axis write proof, cost-shape grid, latency curve; matrix: drain rate, interference sweep, read cost), the proof model (`proven`/`unproven`/`absent` per value, `check` exit 7 when any requested value is not proven), the ceiling history, and the `iobench` CLI over cli-extended (`target init|show`, `calibrate`, `matrix`, `run`, `check`, `show`, `ceilings add|summary`, `restore`).
- Safety: dedicated targets with a marker (device or preallocated file), namespace proof, restore journal with the coupled dirty-ratio knobs, io.max watch, abort guard, single instance, orphan-fio refusal.

### Changed (pre-release review, 2026-10-27)
- The Host binds argv independently of the caller (`check_argv`, RealHost and FakeHost): `blkdiscard`/`fallocate -p` only through `discard_range`; a fio writer through a plain `run`/`spawn` only to an `iobench-*`-named load file inside a scratch root the run authorised (`Host.allow_scratch`: the run's log directory and the verified work dir; symlinks resolved, never a device node or a symlink, `:`/`,`/whitespace/control characters/`..` refused); every fio log prefix is bound to the same roots, bound or not; numeric fio values are non-negative integers; `--rw` limited to `read|write|randread|randwrite` (`trim*` would discard); the `systemd-run` argv is exactly the probe-unit shape with the probe script compared byte for byte (`iobench.probe_source`, a new leaf module) and its directory/output bound to the roots.
- `s0` creates a new work dir (previously a "new" work dir had to exist for fio to create its load files).
- Round 3: every non-fio argv is pinned to its builder in length and slots (`fallocate -l 1 -p /dev/sda` and every `-d`/`-z`/`--punch-hole` form refused); `fallocate -l` runs only through the new bound `Host.preallocate` (a plain `run` refuses every fallocate); `RuntimeMaxSec` positive and <= 600 s; slice names never start with `-`; fio magnitudes capped; `/run/iobench/<run_id>/` is cleared after every run (IOB-BL-09 closed); the cmru release notes carry the caveats.
- Round 4: `Host.preallocate` is contained (inside the directory `target init` verified and authorised; absent or a regular file, never a device node or symlink; `path_unclean` refusal); the scratch cleanup removes only the fio log files iobench named and can never raise out of `cleanup` (a failure is `restore.warnings[]: scratch_not_cleared`, the lock is always released, the verdict never changes); a fio `--name` is one clean token. `target init --file` authorises its directory only after every check passed (revoked on refusal), refuses pseudo/volatile filesystems (`devtmpfs`, `devpts`, `proc`, `sysfs`) before the UUID check, and the scratch cleanup accepts fio's `.N.log` numbering. The containment is check-then-exec (stated in SPEC ARC-05).
- Round 6 review nits: test KeyboardInterrupt/SystemExit propagation and root revocation; clarify IOB-ARC-05 and implementation notes.
- The matrix removes a cell's load files behind the cell (the work-dir check sizes one cell), and refuses a work dir on a filesystem with no single block device behind it (`workdir_disk_unknown`) instead of failing mid-run.
- A protected cgroup whose `io.pressure` becomes unreadable mid-run aborts the run (exit 8, restored) instead of ending it with a traceback.
- `probe_failed` also covers a probe binary that is missing; `production_pattern_invalid` for a malformed `--production-pattern`; the orphan-fio scan tolerates a non-UTF-8 `/proc/<pid>/cmdline`; the restore journal lists each file once.
- RealHost: the parent-death `prctl` is resolved before the fork (not inside `preexec_fn`); a file target's marker is opened `O_NOFOLLOW`.

### Known limitations
- `check` exit 7 is the expected answer for `caps` and `iocost_model` until a `seq1m` ceiling history exists (IOB-BL-03, IOB-BL-07 R3-2); the abort guard watches IO pressure only (IOB-BL-12); cross-direction ceiling movement is a blind spot (IOB-BL-04). See `BACKLOG.md`.
<!-- cmru: pending release notes end -->
